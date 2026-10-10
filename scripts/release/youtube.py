#!/usr/bin/env python3
"""Upload a film's videos to YouTube and keep them matching release/youtube.yaml, from a laptop or a CI job.

Metadata, thumbnails, captions and playlists are declared in the config; `apply` makes the channel match it.
Built on Google's official Python client (google-api-python-client, google-auth).

Usage:
  bin/tool youtube auth --client-secrets FILE [--env FILE]          once, on a laptop: consent, then write the three YT_* lines
  bin/tool youtube whoami                                           which channel the token acts for
  bin/tool youtube upload FILE --key KEY [--config release/youtube.yaml] [--force]
  bin/tool youtube apply [--config release/youtube.yaml] [--only KEY] [--dry-run] [--force] [--strict]
  bin/tool youtube list [--config release/youtube.yaml]
  bin/tool youtube keepalive                                        one cheap call; a weekly cron keeps the token in use
Standalone, no production layout and no bin/tool (a CI job, from a checkout root):
  python3 scripts/release/youtube.py apply --config release/youtube.yaml
--text and --media (bin/tool passes them first) default to the current folder; thumbnails and captions are read
relative to the media root, then the text root; a description_file relative to the text root.

Credentials: YT_CLIENT_ID, YT_CLIENT_SECRET and YT_REFRESH_TOKEN, from the environment; any that is missing is read
from the file named by $YT_ENV, else <text root>/.env (KEY=VALUE lines). None of them is ever printed. One scope is
used (youtube.force-ssl: upload, update, captions, playlists). Exit codes: 0 done; 1 an error (the message says
which); 2 usage, config or credentials; 3 stopped at once and never retried: the daily quota or upload limit is
spent, or the refresh token is dead (invalid_grant); 4 refused by a guard (see apply).

The config (default release/youtube.yaml in the text root; a key is yours, the ids are YouTube's):
  channel:   UC…                      optional; when set, upload, apply and list first check the token acts for it (refuse if not)
  defaults:  {category: 1, language: en, privacy: private, made_for_kids: false, synthetic: true, license: youtube, notify: false}
  playlists:                          created on first use, their id written back
    versions: {title: …, description: …, privacy: public, id: PL…}
  videos:
    teaser-v5:
      id: abc123                      written by upload
      file: …                         informational
      title: …
      description: |                  or description_file: release/youtube/teaser.txt
      tags: [a, b]
      privacy: unlisted               public | unlisted | private
      publish_at: 2026-10-11T11:11:00-07:00     a plain scheduled publish: sent as private until then (give the offset)
      thumbnail: path                 (jpg or png under 2 MB)
      captions: {en: path.srt}        one file per language
      playlists: [versions]
    other fields: category, language, made_for_kids, license (youtube | creativeCommon), embeddable, notify (upload only),
    synthetic (status.containsSyntheticMedia: the realistic-AI-content disclosure); each falls back to `defaults`.
upload needs made_for_kids and synthetic set (on the video or in defaults): they are declarations to YouTube, never
guessed. Its privacy is private unless the config says otherwise. Right after the upload the new id is written into the
YAML (a one-line edit of that key's `id:`, every other byte of the file untouched) and `apply` runs for that key.
Nothing is ever deleted. Removing publish_at from the config does not cancel a schedule already set on YouTube (the live
value is carried over): change it in Studio, or give a new publish_at.

apply, per video that has an id: one videos.list for all of them, then ONE videos.update when any of title, description,
tags, categoryId, defaultLanguage, privacyStatus, publishAt, selfDeclaredMadeForKids, license, embeddable or
containsSyntheticMedia differs. An update replaces its parts, so it sends the whole snippet and status, carrying over live
values the config does not set; containsSyntheticMedia is compared only when the live video returns it. A video that is
public now is never made unlisted, private or scheduled by apply unless --force (a future publish_at or a default of
private would take it down): the config's privacy is ignored with a note.
A thumbnail or a caption file is sent only when its sha256 differs from the one recorded for that video in
release/youtube.state.json (next to the config; commit it, or keep it between CI runs in a cache). A key re-uploaded as a
new video starts with nothing recorded. A configured thumbnail or caption file that is not on disk (media is not in git,
so not in a CI checkout) is a note and skipped, not an error; --strict makes it one. A language that already has a
standard caption track gets captions.update, else captions.insert. Playlists named by a video are created if they have
no id, and the video is added if the playlist lacks it.
Guard: each public flip (a video's privacy going to public, or a publish_at within 24 h) is recorded in the state file
with its time. More than 3 inside a trailing 24 h, the earlier ones counted from the record, is refused (exit 4)
without --force, because a burst of publishes pauses subscriber notifications. A state file lost between CI runs resets
that count to zero. --dry-run prints the diff and the quota it would spend and calls no write.

Quota units (10,000 a day; videos.insert is a separate bucket of 100 calls): list 1, update 50, thumbnails.set 50,
captions.list 50, captions.insert 400, captions.update 450, playlists.insert 50, playlistItems.list 1,
playlistItems.insert 50. The total spent is printed at the end.
"""
import argparse
import datetime
import hashlib
import http.client
import json
import os
import random
import re
import sys
import tempfile
import time
import warnings

HERE = os.path.dirname(os.path.realpath(__file__))
SCRIPTS = os.path.dirname(HERE)
sys.path.insert(0, SCRIPTS)
from lib import cli, production  # noqa: E402

# google-auth warns on every import under an end-of-life Python (the laptop's); the tool is no louder for it.
warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', message='urllib3 v2 only supports OpenSSL')   # the laptop's LibreSSL python

SCOPE = 'https://www.googleapis.com/auth/youtube.force-ssl'
KEYS =('YT_CLIENT_ID', 'YT_CLIENT_SECRET', 'YT_REFRESH_TOKEN')
CHUNK = 16 * 1024 * 1024
RETRIES = 5
RETRY_STATUS = (500, 502, 503, 504)
# Google sends quotaExceeded as a 403 and uploadLimitExceeded as a 400: the reason decides, not the status.
STOP_REASONS = ('quotaExceeded', 'uploadLimitExceeded', 'dailyLimitExceeded')
SHOWN = ('title', 'description', 'tags', 'categoryId', 'defaultLanguage', 'privacyStatus', 'publishAt',
         'selfDeclaredMadeForKids', 'license', 'embeddable', 'containsSyntheticMedia')
# What videos.update accepts back from a live video: it replaces each part, so these are carried over.
SNIPPET_KEEP = ('title', 'description', 'tags', 'categoryId', 'defaultLanguage', 'defaultAudioLanguage')
STATUS_KEEP = ('privacyStatus', 'publishAt', 'license', 'embeddable', 'publicStatsViewable',
               'selfDeclaredMadeForKids', 'containsSyntheticMedia')
BOOLS = ('selfDeclaredMadeForKids', 'embeddable', 'containsSyntheticMedia')
VIDEO_KEYS = ('id', 'file', 'title', 'description', 'description_file', 'tags', 'category', 'language', 'privacy',
              'publish_at', 'made_for_kids', 'license', 'embeddable', 'notify', 'synthetic', 'thumbnail',
              'captions', 'playlists')
DEFAULT_KEYS = ('category', 'language', 'privacy', 'made_for_kids', 'license', 'notify', 'embeddable', 'synthetic')
PLAYLIST_KEYS = ('id', 'title', 'description', 'privacy')
CONFIG_KEYS = ('channel', 'defaults', 'playlists', 'videos')
OK, FAIL, USAGE, STOPPED, REFUSED = 0, 1, 2, 3, 4

_sleep = time.sleep   # tests replace it
_now = lambda: datetime.datetime.now(datetime.timezone.utc)  # noqa: E731


class Stop(Exception):
    """The run must end now and never retry: the quota is spent or the token is dead."""


def die(code, msg):
    print(msg, file=sys.stderr)
    sys.exit(code)


def say(msg=''):
    print(msg, flush=True)


# ---------------------------------------------------------------------------------------------- credentials

def parse_env(path):
    out = {}
    with open(path, encoding='utf-8') as fh:
        for ln in fh:
            m = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$', ln)
            if m:
                v = m.group(2)
                if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                    v = v[1:-1]
                out[m.group(1)] = v
    return out


def env_path(r):
    return os.environ.get('YT_ENV') or os.path.join(r.text, '.env')


def credentials(r):
    vals = {k: os.environ.get(k) for k in KEYS}
    if not all(vals.values()):
        p = env_path(r)
        if os.path.isfile(p):
            found = parse_env(p)
            vals = {k: vals[k] or found.get(k) for k in KEYS}
    if not all(vals.values()):
        die(USAGE, 'youtube: credentials missing: set YT_CLIENT_ID, YT_CLIENT_SECRET and YT_REFRESH_TOKEN in the '
                   'environment, or in the file named by $YT_ENV (else <text root>/.env); `youtube auth` writes them')
    return vals


def write_env(path, values):
    """Set the given KEY=VALUE lines in `path`: other lines are kept, a repeated key keeps its first line, the file
    ends up mode 0600. If `path` is a symlink (a shared vault file) the link stays and its target is written; a link
    whose target is missing is refused. Written through a temp file beside the real target and renamed, so an
    interrupted write leaves the old file, and the temp file never outlives the call."""
    real = os.path.realpath(path)
    if os.path.islink(path) and not os.path.exists(path):
        die(FAIL, f'youtube: {path} is a symlink to {real}, which does not exist (a vault that is not mounted?): '
                  'nothing written')
    lines = []
    if os.path.isfile(real):
        with open(real, encoding='utf-8') as fh:
            lines = fh.read().split('\n')
        if lines and lines[-1] == '':
            lines.pop()
    out, seen = [], set()
    for ln in lines:
        m = re.match(r'^\s*(?:export\s+)?(YT_[A-Z_]+)\s*=', ln)
        if m and m.group(1) in values:
            if m.group(1) not in seen:
                out.append(f'{m.group(1)}={values[m.group(1)]}')
                seen.add(m.group(1))
        else:
            out.append(ln)
    out += [f'{k}={v}' for k, v in values.items() if k not in seen]
    fd, tmp = tempfile.mkstemp(prefix='.' + os.path.basename(real) + '.', dir=os.path.dirname(real))   # mode 0600
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(out) + '\n')
        os.replace(tmp, real)
        os.chmod(real, 0o600)
    finally:
        if os.path.lexists(tmp):
            os.remove(tmp)


# ---------------------------------------------------------------------------------------------- the client

def libs():
    """The Google client's error types, imported late so --help and the CI refusal need no packages."""
    try:
        import httplib2
        from google.auth.exceptions import RefreshError
        from googleapiclient.errors import HttpError
    except ImportError:
        die(USAGE, 'youtube: needs the Google client: pip3 install --user google-api-python-client google-auth '
                   '(and google-auth-oauthlib, for auth alone)')
    return HttpError, RefreshError, httplib2


def make_service(creds):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    # google-auth fills in Google's token endpoint itself
    c = Credentials.from_authorized_user_info({'client_id': creds['YT_CLIENT_ID'], 'client_secret': creds['YT_CLIENT_SECRET'],
                                               'refresh_token': creds['YT_REFRESH_TOKEN']}, scopes=[SCOPE])
    return build('youtube', 'v3', credentials=c, cache_discovery=False)


def http_status(e):
    return int(getattr(getattr(e, 'resp', None), 'status', 0) or 0)


def error_info(e):
    """(reason, message) out of an HttpError's JSON body, '' where it has none."""
    try:
        err = json.loads(e.content.decode('utf-8', 'replace'))['error']
        first = (err.get('errors') or [{}])[0]
        return first.get('reason', ''), err.get('message', '')
    except (ValueError, KeyError, AttributeError, TypeError):
        return '', ''


def describe(e):
    HttpError = libs()[0]
    if isinstance(e, HttpError):
        reason, message = error_info(e)
        return f'HTTP {http_status(e)} {reason} {message}'.strip()
    return f'{type(e).__name__}: {str(e)[:160]}'


def refresh_code(e):
    """The error code the token endpoint gave (invalid_grant, invalid_client, …) out of a RefreshError, '' if none.
    Only the code is read: the message and the response are never printed, they can carry request detail."""
    for part in e.args[1:2]:
        if isinstance(part, dict) and isinstance(part.get('error'), str):
            return part['error']
    m = re.match(r'^([a-z_]+)\b', str(e.args[0]) if e.args else '')
    return m.group(1) if m else ''


def stop_reason(e):
    """A reason to end the whole run without retrying, or None."""
    HttpError, RefreshError, _ = libs()
    if isinstance(e, HttpError) and http_status(e) in (400, 403) and error_info(e)[0] in STOP_REASONS:
        reason = error_info(e)[0]
        if reason == 'uploadLimitExceeded':
            return f'{reason}: the channel has hit its upload limit; try again later'
        return f'{reason}: today\'s quota is spent; try again after midnight Pacific time'
    if isinstance(e, RefreshError):
        code = refresh_code(e)
        if code == 'invalid_grant':
            return 'token dead (invalid_grant): re-run `bin/tool youtube auth` on a laptop and reset YT_REFRESH_TOKEN'
        if code == 'invalid_client':
            return ('the OAuth client was rejected (invalid_client): check YT_CLIENT_ID and YT_CLIENT_SECRET '
                    '(the refresh token itself may be fine)')
        return f'the token could not be refreshed ({code or "no error code given"}): nothing else was tried'
    return None


def retriable(e):
    HttpError, _, httplib2 = libs()
    if isinstance(e, HttpError):
        return http_status(e) in RETRY_STATUS
    return isinstance(e, (OSError, httplib2.HttpLib2Error, http.client.HTTPException))


# ---------------------------------------------------------------------------------------------- config and state

def load_config(a, r, need=True):
    path = r.t(a.config)
    if not os.path.isfile(path):
        if need:
            die(USAGE, f'youtube: no config at {path}: release/youtube.yaml names the videos (see --help for its shape)')
        return {}, path
    cfg = production.load_yaml(path) or {}
    if not isinstance(cfg, dict):
        die(USAGE, f'youtube: {path} is not a mapping')
    for k in cfg:
        if k not in CONFIG_KEYS:
            die(USAGE, f'youtube: {path}: unknown top-level key `{k}` (one of {", ".join(CONFIG_KEYS)})')
    for sect, known in (('defaults', DEFAULT_KEYS),):
        for k in cfg.get(sect) or {}:
            if k not in known:
                die(USAGE, f'youtube: {path}: unknown {sect} key `{k}` (one of {", ".join(known)})')
    for name, known in (('playlists', PLAYLIST_KEYS), ('videos', VIDEO_KEYS)):
        sect = cfg.get(name) or {}
        if not isinstance(sect, dict):
            die(USAGE, f'youtube: {path}: `{name}` must be a mapping of keys')
        for key, body in sect.items():
            if not isinstance(body, dict):
                die(USAGE, f'youtube: {path}: {name}.{key} must be a mapping')
            for k in body:
                if k not in known:
                    die(USAGE, f'youtube: {path}: {name}.{key}: unknown field `{k}` (one of {", ".join(known)})')
        cfg[name] = sect
    cfg['defaults'] = cfg.get('defaults') or {}
    return cfg, path


def state_path(cfgpath):
    base = cfgpath[:-5] if cfgpath.endswith('.yaml') else cfgpath
    return base + '.state.json'


def load_state(path):
    if not os.path.isfile(path):
        return {}
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def save_state(path, state):
    # a flip older than a day no longer counts toward the guard
    if state.get('_flips'):
        keep = recent_flips(state)
        state['_flips'] = [rfc3339(d) for d in sorted(keep)]
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write('\n')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _scalar(value):
    import yaml
    if re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_-]*', value) and yaml.safe_load(value) == value:
        return value
    return json.dumps(value)


def flow_pairs(inner):
    """The (start, end) spans of the top-level `key: value` pairs of a one-line flow mapping, given as the text from
    its `{` up to (not including) its closing `}`. Commas and colons inside quoted strings or nested brackets are not
    separators, so a title like "Cuts, id: by date" is never mistaken for a pair."""
    spans, depth, quote, start, i = [], 0, None, None, 0
    while i < len(inner):
        c = inner[i]
        if quote:
            if c == '\\' and quote == '"':
                i += 1
            elif c == quote:
                quote = None
        elif c in '"\'' and inner[:i].rstrip()[-1:] in (':', ',', '{', '['):   # an apostrophe in a plain word is not a quote
            quote = c
        elif c in '{[':
            depth += 1
            if depth == 1:
                start = i + 1
        elif c in '}]':
            depth -= 1
        elif c == ',' and depth == 1:
            spans.append((start, i))
            start = i + 1
        i += 1
    if start is not None:
        spans.append((start, len(inner)))
    return spans


def set_field(path, section, key, field, value):
    """Write `field: value` under section.key in the YAML at `path` by editing that key's lines only, so comments,
    ordering and quoting elsewhere survive (PyYAML would drop the comments). Handles a block key and a one-line
    `{…}` key. The result is re-parsed; if it does not hold the value the file is left as it was and this raises."""
    import yaml
    with open(path, encoding='utf-8') as fh:
        text = fh.read()
    lines = text.split('\n')
    val = _scalar(value)

    def indent(s):
        return len(s) - len(s.lstrip(' '))

    def live(s):
        return s.strip() != '' and not s.lstrip().startswith('#')

    head = next((i for i, s in enumerate(lines) if re.match(rf'^{re.escape(section)}\s*:\s*(#.*)?$', s)), None)
    if head is None:
        raise ValueError(f'no top-level `{section}:` line')
    end = len(lines)
    for i in range(head + 1, len(lines)):
        if live(lines[i]) and indent(lines[i]) == 0:
            end = i
            break
    kids = [i for i in range(head + 1, end) if live(lines[i])]
    if not kids:
        raise ValueError(f'`{section}:` has no entries')
    ind = indent(lines[kids[0]])
    kre = re.compile(rf'^( {{{ind}}})(["\']?){re.escape(key)}\2\s*:(.*)$')
    at = next((i for i in kids if kre.match(lines[i]) and indent(lines[i]) == ind), None)
    if at is None:
        raise ValueError(f'no `{key}:` under `{section}:`')
    rest = kre.match(lines[at]).group(3)
    if rest.strip().startswith('{'):
        close = rest.rfind('}')
        if close < 0:
            raise ValueError(f'`{key}` is a flow mapping that does not close on its line')
        inner = rest[:close]
        hit = next(((a, b) for a, b in flow_pairs(inner) if re.match(r'\s*id\s*:', inner[a:b])), None)
        if hit:
            a, b = hit
            seg = inner[a:b]
            lead, trail = seg[:len(seg) - len(seg.lstrip())], seg[len(seg.rstrip()):]
            inner = inner[:a] + f'{lead}id: {val}{trail}' + inner[b:]
        elif inner.strip() == '{' or not any(inner[a:b].strip() for a, b in flow_pairs(inner)):
            inner = inner.rstrip() + f'id: {val}'
        else:
            inner = inner.rstrip() + f', id: {val}'
        lines[at] = lines[at][:len(lines[at]) - len(rest)] + inner + rest[close:]
    elif rest.strip() == '' or rest.strip().startswith('#'):
        block = [i for i in range(at + 1, end) if live(lines[i])]
        block = block[:next((n for n, i in enumerate(block) if indent(lines[i]) <= ind), len(block))]
        cind = indent(lines[block[0]]) if block else ind + 2
        hit = next((i for i in block if indent(lines[i]) == cind and re.match(r'^\s*id\s*:', lines[i])), None)
        if hit is not None:
            m = re.match(r'^(\s*id\s*:)\s*(\S.*?)?(\s+#.*)?$', lines[hit])
            lines[hit] = f'{m.group(1)} {val}{m.group(3) or ""}'
        else:
            lines.insert(at + 1, ' ' * cind + f'id: {val}')
    else:
        raise ValueError(f'`{key}` is not a mapping')
    new = '\n'.join(lines)
    try:
        got = ((yaml.safe_load(new) or {}).get(section) or {}).get(key, {}).get('id')
    except (yaml.YAMLError, AttributeError) as e:
        raise ValueError(f'the edited file does not parse as expected ({type(e).__name__})')
    if str(got) != value:
        raise ValueError('the edited file does not hold the new id')
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(new)
    os.chmod(tmp, os.stat(path).st_mode & 0o7777)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------------------------- what the config wants

_TIME = re.compile(r'^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.\d+)?\s*(Z|[+-]\d\d:?\d\d)?$')


def parse_time(x):
    """A config or API date-time as an aware UTC datetime to the second. A time without an offset is taken as UTC."""
    if isinstance(x, datetime.datetime):
        d = x
    elif isinstance(x, datetime.date):
        d = datetime.datetime(x.year, x.month, x.day)
    else:
        m = _TIME.match(str(x).strip())
        if not m:
            die(USAGE, f'youtube: not a date-time (want 2026-10-11T11:11:00-07:00): {x!r}')
        y, mo, dd, h, mi, s, tz = m.groups()
        d = datetime.datetime(int(y), int(mo), int(dd), int(h), int(mi), int(s))
        if tz and tz != 'Z':
            sign = -1 if tz[0] == '-' else 1
            hh, mm = int(tz[1:3]), int(tz[-2:])
            d = d.replace(tzinfo=datetime.timezone(sign * datetime.timedelta(hours=hh, minutes=mm)))
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.astimezone(datetime.timezone.utc).replace(microsecond=0)


def rfc3339(d):
    return d.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def norm_text(s):
    return (s or '').replace('\r\n', '\n').rstrip()


def resolve(r, p):
    """A media file the config names: absolute as is, else under the media root, else the text root."""
    if os.path.isabs(p):
        return p
    for root in (r.media, r.text):
        if os.path.isfile(os.path.join(root, p)):
            return os.path.join(root, p)
    return os.path.join(r.media, p)


def desired(r, cfg, key, v, note=print):
    """The fields the config sets for one video, as {'snippet': {…}, 'status': {…}} in the API's names; a field the
    config leaves unset is absent, so an update carries the live value over."""
    d = cfg['defaults']

    def pick(k):
        return v[k] if k in v else d.get(k)

    sn, st = {}, {}
    if v.get('title') is not None:
        sn['title'] = str(v['title'])
    if v.get('description_file'):
        try:
            with open(os.path.join(r.text, v['description_file']), encoding='utf-8') as fh:
                sn['description'] = norm_text(fh.read())
        except OSError as e:
            die(USAGE, f'youtube: {key}: description_file: {e.strerror}: {v["description_file"]}')
    elif v.get('description') is not None:
        sn['description'] = norm_text(str(v['description']))
    if v.get('tags') is not None:
        sn['tags'] = [str(t) for t in v['tags']]
    if pick('category') is not None:
        sn['categoryId'] = str(pick('category'))
    if pick('language') is not None:
        sn['defaultLanguage'] = str(pick('language'))
    at = parse_time(v['publish_at']) if v.get('publish_at') is not None else None
    if at and at > _now():
        st['privacyStatus'], st['publishAt'] = 'private', rfc3339(at)
    else:
        if at:
            note(f'publish_at {rfc3339(at)} has passed: left to the video\'s live state')
        # a passed schedule has made the video public; the default privacy must not pull it back to private
        privacy = v.get('privacy') if at else pick('privacy')
        if privacy is not None:
            st['privacyStatus'] = str(privacy)
    if st.get('privacyStatus') not in (None, 'public', 'unlisted', 'private'):
        die(USAGE, f'youtube: {key}: privacy must be public, unlisted or private, not {st["privacyStatus"]!r}')
    if pick('made_for_kids') is not None:
        st['selfDeclaredMadeForKids'] = bool(pick('made_for_kids'))
    if pick('license') is not None:
        st['license'] = str(pick('license'))
        if st['license'] not in ('youtube', 'creativeCommon'):
            die(USAGE, f'youtube: {key}: license must be youtube or creativeCommon, not {st["license"]!r}')
    if pick('embeddable') is not None:
        st['embeddable'] = bool(pick('embeddable'))
    if pick('synthetic') is not None:
        st['containsSyntheticMedia'] = bool(pick('synthetic'))
    return {'snippet': sn, 'status': st}


def same(k, live, want):
    if k == 'publishAt':
        return live is not None and parse_time(live) == parse_time(want)
    if k == 'description':
        return norm_text(live) == norm_text(want)
    if k == 'tags':
        return sorted(live or []) == sorted(want or [])
    if k == 'categoryId':
        return str(live) == str(want)
    if k in BOOLS:
        return bool(live) == bool(want)
    return live == want


def diff(item, want):
    out = []
    for part in ('snippet', 'status'):
        for k, w in want[part].items():
            # not every videos.list answer carries the AI-content flag: with no live value to compare, never update for it
            if k == 'containsSyntheticMedia' and k not in item.get(part, {}):
                continue
            if not same(k, item.get(part, {}).get(k), w):
                out.append((part, k, item.get(part, {}).get(k), w))
    return out


def full_body(vid, item, want):
    """videos.update replaces each part it is given: the live writable fields, overlaid with the config's."""
    sn = {k: item['snippet'][k] for k in SNIPPET_KEEP if k in item.get('snippet', {})}
    st = {k: item['status'][k] for k in STATUS_KEEP if k in item.get('status', {})}
    sn.update(want['snippet'])
    st.update(want['status'])
    # a publish time means something only on a private video: a video made visible now loses its schedule
    if st.get('privacyStatus') != 'private' and 'publishAt' not in want['status']:
        st.pop('publishAt', None)
    return {'id': vid, 'snippet': sn, 'status': st}


def short(x, n=44):
    s = ' '.join(str(x).split()) if not isinstance(x, (list, dict)) else json.dumps(x, ensure_ascii=False)
    return repr(s if len(s) <= n else s[:n - 1] + '…')


# ---------------------------------------------------------------------------------------------- the run

class Run:
    def __init__(self, yt, a, r, cfg, cfgpath):
        self.yt, self.a, self.r, self.cfg, self.cfgpath = yt, a, r, cfg, cfgpath
        self.dry = bool(getattr(a, 'dry_run', False))
        self.spent = self.planned = self.inserts = 0
        self.errors = []

    def call(self, req, cost, retries=0, insert=False):
        """Execute one request, counting its quota. Idempotent reads and updates pass retries; an insert never does
        (a retried insert can make two). A spent quota or a dead token ends the run as Stop."""
        if insert:
            self.inserts += 1
        else:
            self.spent += cost
        try:
            return req.execute(num_retries=retries)
        except Exception as e:  # noqa: BLE001 - classified, then re-raised
            why = stop_reason(e)
            if why:
                raise Stop(why)
            raise

    def do(self, label, cost, make, retries=0, insert=False):
        """A write: executed, or in a dry run only announced and costed."""
        if self.dry:
            say(f'  would {label}  ({cost} units)')
            self.planned += cost
            return None
        out = self.call(make(), cost, retries, insert)
        say(f'  {label}')
        return out

    def error(self, msg):
        self.errors.append(msg)
        print(f'  ERROR {msg}', file=sys.stderr)

    def check_channel(self):
        want = self.cfg.get('channel')
        if not want:
            return
        got = channel(self)
        if got['id'] != want:
            die(REFUSED, f'youtube: the token acts for channel {got["id"]} ({got["snippet"]["title"]}), '
                         f'the config says {want}: refusing to touch it')

    def summary(self):
        s = f'quota: {self.spent} units spent'
        if self.inserts:
            s += f', {self.inserts} videos.insert (of 100 a day)'
        if self.dry:
            s += f'; {self.planned} more units if run for real'
        say(s)

    def finish(self):
        self.summary()
        if self.errors:
            print(f'youtube: {len(self.errors)} error(s), see ERROR above', file=sys.stderr)
            return FAIL
        return OK


def channel(run):
    res = run.call(run.yt.channels().list(part='id,snippet', mine=True), 1, retries=3)
    items = res.get('items') or []
    if not items:
        die(FAIL, 'youtube: the token has no YouTube channel')
    return items[0]


def fetch(run, ids, part):
    got = {}
    for i in range(0, len(ids), 50):
        res = run.call(run.yt.videos().list(part=part, id=','.join(ids[i:i + 50])), 1, retries=3)
        for it in res.get('items') or []:
            got[it['id']] = it
    return got


def thumb_mime(path):
    return 'image/png' if path.lower().endswith('.png') else 'image/jpeg'


def sync_thumb(run, vid, path):
    from googleapiclient.http import MediaFileUpload
    run.do('thumbnails.set', 50, lambda: run.yt.thumbnails().set(
        videoId=vid, media_body=MediaFileUpload(path, mimetype=thumb_mime(path))), retries=3)


def sync_caption(run, vid, lang, path):
    from googleapiclient.http import MediaFileUpload
    if run.dry:
        say(f'  would send captions [{lang}] (captions.insert 400 units, or captions.update 450 if a track exists; '
            'the track lookup is skipped in a dry run)')
        run.planned += 400
        return
    tracks = run.call(run.yt.captions().list(part='snippet', videoId=vid), 50, retries=3).get('items') or []
    have = next((t for t in tracks if t['snippet'].get('language') == lang
                 and str(t['snippet'].get('trackKind', 'standard')).lower() == 'standard'), None)
    media = lambda: MediaFileUpload(path, mimetype='application/octet-stream')  # noqa: E731
    if have:
        run.do(f'captions.update [{lang}]', 450, lambda: run.yt.captions().update(
            part='snippet', body={'id': have['id'], 'snippet': {'isDraft': False}}, media_body=media()))
    else:
        run.do(f'captions.insert [{lang}]', 400, lambda: run.yt.captions().insert(
            part='snippet', body={'snippet': {'videoId': vid, 'language': lang, 'name': '', 'isDraft': False}},
            media_body=media()))


def sync_playlist(run, vid, pkey):
    pls = run.cfg['playlists']
    if pkey not in pls:
        run.error(f'playlist `{pkey}` is not under `playlists:` in {run.cfgpath}')
        return
    pl = pls[pkey]
    pid = pl.get('id')
    if not pid:
        if not pl.get('title'):
            run.error(f'playlist `{pkey}` has no id and no title to create it with')
            return
        body = {'snippet': {'title': str(pl['title']), 'description': str(pl.get('description') or '')},
                'status': {'privacyStatus': str(pl.get('privacy') or 'private')}}
        made = run.do(f'playlists.insert `{pkey}`', 50, lambda: run.yt.playlists().insert(
            part='snippet,status', body=body))
        if made is None:
            say(f'  would add to `{pkey}`  (50 units)')
            run.planned += 50
            return
        pid = pl['id'] = made['id']
        try:
            set_field(run.cfgpath, 'playlists', pkey, 'id', pid)
        except Exception as e:  # noqa: BLE001 - a YAML error, a missing key, a read-only file: the id must not be lost
            run.error(f'playlist `{pkey}` was created as {pid} but could not be written to {run.cfgpath} ({e}): '
                      f'add `id: {pid}` under playlists.{pkey} by hand')
    found = run.call(run.yt.playlistItems().list(part='id', playlistId=str(pid), videoId=vid, maxResults=1), 1, retries=3)
    if found.get('items'):
        return
    run.do(f'playlistItems.insert `{pkey}`', 50, lambda: run.yt.playlistItems().insert(
        part='snippet', body={'snippet': {'playlistId': str(pid), 'resourceId': {'kind': 'youtube#video', 'videoId': vid}}}))


def is_flip(item, want):
    """When would this update make the video visible: now (its privacy going public), at its scheduled time (a
    publish_at within 24 h), or never (None)?"""
    st = want['status']
    if st.get('privacyStatus') == 'public' and item.get('status', {}).get('privacyStatus') != 'public':
        return _now()
    if 'publishAt' in st and not same('publishAt', item.get('status', {}).get('publishAt'), st['publishAt']):
        at = parse_time(st['publishAt'])
        if at <= _now() + datetime.timedelta(hours=24):
            return at
    return None


def recent_flips(state):
    """The public flips recorded in the state file that fall in the trailing 24 h (or are scheduled inside the next)."""
    now, out = _now(), []
    for t in state.get('_flips') or []:
        if isinstance(t, str) and _TIME.match(t.strip()):
            d = parse_time(t)
            if now - datetime.timedelta(hours=24) < d <= now + datetime.timedelta(hours=24):
                out.append(d)
    return out


def guard(run, state, labels):
    """Refuse a fourth public flip inside 24 h without --force (a burst of publishes pauses subscriber notifications).
    The count is the state file's record plus this run's: a CI run that has lost the state file starts from zero."""
    total = len(recent_flips(state)) + len(labels)
    if total <= 3:
        return
    msg = (f'youtube: {total} videos would be public within a day ({len(recent_flips(state))} recorded in the last '
           f'24 h, plus {", ".join(labels)}): more than 3 pauses subscriber notifications for a day; '
           'run with --force to go ahead')
    if not getattr(run.a, 'force', False):
        run.summary()
        die(REFUSED, msg)
    print(msg.split(':', 1)[0] + ': --force given, going ahead', file=sys.stderr)


def entry_for(state, key, vid):
    """The state entry for a key. One recorded for another video (the key was re-uploaded) is replaced: its shas
    describe files sent to a video that is no longer this one."""
    e = state.get(key)
    if not isinstance(e, dict) or e.get('video') != vid:
        e = state[key] = {'video': vid}
    return e


def protect_public(key, item, want, force, notes):
    """A video that is public now is not made unlisted, private or scheduled by a config that merely says so
    (a future publish_at, or a default of private) unless --force: that would take it down."""
    live_privacy = item.get('status', {}).get('privacyStatus')
    wanted = want['status'].get('privacyStatus')
    if live_privacy == 'public' and wanted not in (None, 'public'):
        if force:
            notes.append(f'--force: this public video is being made {wanted}')
        else:
            want['status'].pop('privacyStatus', None)
            want['status'].pop('publishAt', None)
            notes.append(f'is public; the config would make it {wanted}: left public (--force makes it so)')


def apply(run, only=None):
    cfg, r = run.cfg, run.r
    force, strict = getattr(run.a, 'force', False), getattr(run.a, 'strict', False)
    videos = cfg['videos']
    if only and only not in videos:
        die(USAGE, f'youtube: no video `{only}` in {run.cfgpath}')
    picked = [(k, v) for k, v in videos.items() if not only or k == only]
    for k, v in picked:
        if not v.get('id'):
            say(f'{k}: no id yet: `youtube upload FILE --key {k}` makes one')
    todo = [(k, v) for k, v in picked if v.get('id')]
    if not todo:
        say('nothing to apply')
        return run.finish()
    live = fetch(run, [str(v['id']) for _, v in todo], 'snippet,status')
    spath = state_path(run.cfgpath)
    state = load_state(spath)
    plans = []
    for k, v in todo:
        vid = str(v['id'])
        if vid not in live:
            run.error(f'{k}: video {vid} is not on this channel (deleted, or another account\'s)')
            continue
        notes = []
        want = desired(r, cfg, k, v, notes.append)
        protect_public(k, live[vid], want, force, notes)
        mine = state.get(k) if isinstance(state.get(k), dict) and state[k].get('video') == vid else {}

        def media(what, name):
            """The file if it is here. A media file the config names is often not in a CI checkout (it is not in git):
            that is a note and a skip, an error only under --strict."""
            p = resolve(r, str(name))
            if os.path.isfile(p):
                return p
            if strict:
                run.error(f'{k}: {what} {p} is not a file')
            else:
                notes.append(f'{what} {name} is not here: skipped')
            return None

        thumb = None
        if v.get('thumbnail'):
            p = media('thumbnail', v['thumbnail'])
            if p and sha256(p) != mine.get('thumb_sha'):
                thumb = (p, sha256(p))
        caps = {}
        for lang, cp in (v.get('captions') or {}).items():
            p = media(f'captions [{lang}]', cp)
            if p and sha256(p) != (mine.get('captions') or {}).get(str(lang)):
                caps[str(lang)] = (p, sha256(p))
        changes = diff(live[vid], want)
        plans.append({'k': k, 'v': v, 'vid': vid, 'item': live[vid], 'want': want, 'changes': changes,
                      'flip': is_flip(live[vid], want) if changes else None, 'thumb': thumb, 'caps': caps,
                      'notes': notes})
    guard(run, state, [p['k'] for p in plans if p['flip']])
    for p in plans:
        k, v, vid, item, want = p['k'], p['v'], p['vid'], p['item'], p['want']
        say(f'{k} ({vid})')
        for n in p['notes']:
            say(f'  note: {n}')
        dirty = False
        try:
            if p['changes']:
                for part, f, old, new in p['changes']:
                    say(f'  {part}.{f}: {short(old)} -> {short(new)}')
                run.do('videos.update', 50, lambda: run.yt.videos().update(
                    part='snippet,status', body=full_body(vid, item, want)), retries=3)
                if p['flip'] and not run.dry:
                    state.setdefault('_flips', []).append(rfc3339(p['flip']))
                    dirty = True
            else:
                say('  metadata up to date')
            if p['thumb']:
                sync_thumb(run, vid, p['thumb'][0])
                if not run.dry:
                    entry_for(state, k, vid)['thumb_sha'] = p['thumb'][1]
                    dirty = True
            for lang, (path, sha) in p['caps'].items():
                sync_caption(run, vid, lang, path)
                if not run.dry:
                    entry_for(state, k, vid).setdefault('captions', {})[lang] = sha
                    dirty = True
            for pk in v.get('playlists') or []:
                sync_playlist(run, vid, str(pk))
        except Stop:
            raise
        except Exception as e:  # noqa: BLE001 - one video's failure must not hide the others
            run.error(f'{k}: {describe(e)}')
        finally:
            if dirty:
                save_state(spath, state)
    return run.finish()


# ---------------------------------------------------------------------------------------------- upload

def send(req):
    """Drive a resumable upload to its end. A 5xx or a dropped connection is retried up to RETRIES times with
    exponential backoff and jitter (the client then asks the server how much arrived, and resumes from there);
    a spent quota, an upload limit or a dead token raises Stop at once."""
    tries = 0
    while True:
        try:
            status, resp = req.next_chunk()
        except Exception as e:  # noqa: BLE001
            why = stop_reason(e)
            if why:
                raise Stop(why)
            if not retriable(e) or tries >= RETRIES:
                raise
            tries += 1
            delay = min(60, 2 ** tries) + random.random()
            print(f'  upload: {describe(e)}; retry {tries}/{RETRIES} in {delay:.0f}s', file=sys.stderr)
            _sleep(delay)
            continue
        if resp is not None:
            return resp
        tries = 0
        if status:
            print(f'  uploaded {int(status.progress() * 100)}%', file=sys.stderr, flush=True)


def cmd_upload(a, r, yt):
    from googleapiclient.http import MediaFileUpload
    cfg, cfgpath = load_config(a, r)
    run = Run(yt, a, r, cfg, cfgpath)
    key = a.key
    v = cfg['videos'].get(key)
    if v is None:
        die(USAGE, f'youtube: no video `{key}` in {cfgpath}: add it under `videos:` first')
    if v.get('id'):
        die(USAGE, f'youtube: `{key}` already has id {v["id"]}; to upload a new video under this key, remove its id line')
    path = resolve(r, a.file)
    if not os.path.isfile(path):
        die(USAGE, f'youtube: {path} is not a file')
    want = desired(r, cfg, key, v, lambda n: say(f'  note: {n}'))
    sn, st = want['snippet'], want['status']
    if 'title' not in sn:
        die(USAGE, f'youtube: `{key}` has no title')
    for field, name in (('selfDeclaredMadeForKids', 'made_for_kids'), ('containsSyntheticMedia', 'synthetic')):
        if field not in st:
            die(USAGE, f'youtube: `{key}`: {name} is not set (on the video or in defaults): it is a declaration to '
                       'YouTube, so the tool never guesses it')
    st.setdefault('privacyStatus', 'private')
    notify = bool(v['notify'] if 'notify' in v else cfg['defaults'].get('notify', False))
    run.check_channel()
    # an upload that is public at once, or scheduled within a day, is a public flip like any other
    spath = state_path(cfgpath)
    state = load_state(spath)
    when = _now() if st['privacyStatus'] == 'public' else (
        parse_time(st['publishAt']) if 'publishAt' in st and parse_time(st['publishAt']) <= _now() + datetime.timedelta(hours=24)
        else None)
    guard(run, state, [key] if when else [])
    req = yt.videos().insert(part='snippet,status', body={'snippet': sn, 'status': st}, notifySubscribers=notify,
                             media_body=MediaFileUpload(path, chunksize=CHUNK, resumable=True))
    run.inserts += 1
    say(f'uploading {os.path.basename(path)} as `{key}` ({st["privacyStatus"]})')
    resp = send(req)
    vid = resp['id']
    say(f'uploaded: video id {vid}  https://youtu.be/{vid}')
    if when:
        state.setdefault('_flips', []).append(rfc3339(when))
        save_state(spath, state)
    try:
        set_field(cfgpath, 'videos', key, 'id', vid)
        say(f'wrote id into {cfgpath}')
    except Exception as e:  # noqa: BLE001 - a YAML error, a missing key, a read-only file: the id must not be lost
        print(f'youtube: the video IS uploaded (id {vid}) but {cfgpath} could not be edited ({e}): '
              f'add `id: {vid}` under videos.{key} by hand', file=sys.stderr)
        run.summary()
        return FAIL
    run.cfg, _ = load_config(a, r)
    return apply(run, only=key)


# ---------------------------------------------------------------------------------------------- the other verbs

def cmd_whoami(a, r, yt):
    cfg, _ = load_config(a, r, need=False)
    run = Run(yt, a, r, cfg or {'defaults': {}, 'playlists': {}, 'videos': {}}, '')
    me = channel(run)
    sn = me['snippet']
    say(f'channel: {sn.get("title", "")}  id {me["id"]}  handle {sn.get("customUrl", "(none)")}')
    if cfg.get('channel'):
        same_ch = cfg['channel'] == me['id']
        say(f'config channel {cfg["channel"]}: {"matches" if same_ch else "DIFFERS"}')
        return OK if same_ch else REFUSED
    return OK


def cmd_keepalive(a, r, yt):
    run = Run(yt, a, r, {'defaults': {}, 'playlists': {}, 'videos': {}}, '')
    me = channel(run)
    say(f'keepalive: token works, channel {me["id"]}')
    return OK


def cmd_apply(a, r, yt):
    cfg, cfgpath = load_config(a, r)
    run = Run(yt, a, r, cfg, cfgpath)
    run.check_channel()
    return apply(run, only=a.only)


def cmd_list(a, r, yt):
    cfg, cfgpath = load_config(a, r)
    run = Run(yt, a, r, cfg, cfgpath)
    run.check_channel()
    ids = [str(v['id']) for v in cfg['videos'].values() if v.get('id')]
    live = fetch(run, ids, 'snippet,status,statistics') if ids else {}
    for k, v in cfg['videos'].items():
        vid = str(v.get('id') or '')
        it = live.get(vid)
        if not vid:
            say(f'{k:<20} (no id yet)')
        elif not it:
            say(f'{k:<20} {vid:<12} NOT FOUND on this channel')
        else:
            st = it.get('status', {})
            say(f'{k:<20} {vid:<12} {st.get("privacyStatus", "?"):<9} {st.get("publishAt", "-"):<22} '
                f'{it.get("statistics", {}).get("viewCount", "-"):>7} views  {it["snippet"]["title"]}')
    run.summary()
    return OK


def cmd_auth(a, r):
    if os.environ.get('CI') or os.environ.get('GITHUB_ACTIONS'):
        die(USAGE, 'youtube auth: refused in CI. Each run of it issues a new refresh token and a client keeps only '
                   'the newest 100, so a job that auths would quietly revoke the real one. Run it on a laptop.')
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        die(USAGE, 'youtube auth: needs pip3 install --user google-auth-oauthlib')
    libs()
    flow = InstalledAppFlow.from_client_secrets_file(a.client_secrets, scopes=[SCOPE])
    say('A browser opens: sign in as the account that owns the channel and pick that channel.')
    creds = flow.run_local_server(port=0, access_type='offline', prompt='consent')
    rt = creds.refresh_token
    if not rt:
        die(FAIL, 'youtube auth: Google returned no refresh token; revoke this app at myaccount.google.com/permissions and run again')
    cid, csec = flow.client_config['client_id'], flow.client_config['client_secret']
    vals = {'YT_CLIENT_ID': cid, 'YT_CLIENT_SECRET': csec, 'YT_REFRESH_TOKEN': rt}
    # through the same path every other verb uses, so the check below also proves the refresh token itself works
    run = Run(make_service(vals), a, r, {'defaults': {}, 'playlists': {}, 'videos': {}}, '')
    try:
        me = channel(run)
    except Stop as s:
        die(STOPPED, f'youtube auth: the new token does not work, nothing written: {s}')
    say(f'channel: {me["snippet"].get("title", "")}  id {me["id"]}')
    target = os.path.abspath(os.path.expanduser(a.env or env_path(r)))
    write_env(target, vals)
    say(f'wrote YT_CLIENT_ID, YT_CLIENT_SECRET and YT_REFRESH_TOKEN to {target} (mode 600)')
    say('For CI, set the same three as repository secrets; never run auth there.')
    return OK


def build_parser():
    ap = cli.parser('youtube', __doc__)
    sub = ap.add_subparsers(dest='verb', metavar='VERB')
    conf = argparse.ArgumentParser(add_help=False)
    conf.add_argument('--config', default=os.path.join('release', 'youtube.yaml'), metavar='FILE',
                      help='the videos (default: release/youtube.yaml in the text root)')
    p = sub.add_parser('auth', help='laptop only: consent, then write the three YT_* lines')
    p.add_argument('--client-secrets', required=True, metavar='FILE', help='the OAuth client JSON downloaded from the console')
    p.add_argument('--env', metavar='FILE', help='where to write them (default: $YT_ENV, else <text root>/.env)')
    sub.add_parser('whoami', parents=[conf], help='which channel the token acts for')
    p = sub.add_parser('upload', parents=[conf], help='upload one file as the video under KEY, then apply it')
    p.add_argument('file')
    p.add_argument('--key', required=True, help='the video\'s key under `videos:`')
    p.add_argument('--force', action='store_true', help='go past the guard against more than 3 public flips in 24 h')
    p = sub.add_parser('apply', parents=[conf], help='make the channel match the config; never deletes')
    p.add_argument('--only', metavar='KEY', help='one video')
    p.add_argument('--dry-run', action='store_true', help='print the diff and the quota, call no write')
    p.add_argument('--force', action='store_true', help='go past the guard against more than 3 public flips in 24 h, '
                   'and let a config make a public video non-public')
    p.add_argument('--strict', action='store_true', help='a thumbnail or captions file that is not here is an error '
                   '(default: a note, and skipped: media is not in a CI checkout)')
    sub.add_parser('list', parents=[conf], help='each configured video: id, live title, privacy, schedule, views')
    sub.add_parser('keepalive', help='one cheap authenticated call, for a weekly job')
    return ap


def main(argv):
    ap = build_parser()
    a = ap.parse_args(argv)
    if not a.verb:
        ap.print_help()
        return USAGE
    r = cli.roots(a)
    if a.verb == 'auth':
        return cmd_auth(a, r)
    creds = credentials(r)
    HttpError = libs()[0]
    verb = {'whoami': cmd_whoami, 'upload': cmd_upload, 'apply': cmd_apply, 'list': cmd_list,
            'keepalive': cmd_keepalive}[a.verb]
    try:
        return verb(a, r, make_service(creds))
    except Stop as s:
        print(f'youtube: stopped: {s}', file=sys.stderr)
        return STOPPED
    except HttpError as e:
        print(f'youtube: {describe(e)}', file=sys.stderr)
        return FAIL


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
