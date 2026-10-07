"""Distributed login throttling with bounded cooldowns, independent of source IP."""
import hashlib,hmac,time

FAILURE_LIMIT=5
WINDOW_SECONDS=15*60
COOLDOWN_SECONDS=5*60

def initialize(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS login_account_throttle (
        account_key TEXT PRIMARY KEY, failures INTEGER NOT NULL,
        window_started INTEGER NOT NULL, locked_until INTEGER NOT NULL
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_login_account_throttle_expiry ON login_account_throttle(window_started,locked_until)')

def account_key(email,secret=''):
    normalized=str(email).strip().lower().encode('utf-8')
    purpose=b'mh-login-account-v1:'+normalized
    return hmac.new(secret.encode(),purpose,hashlib.sha256).hexdigest() if secret else hashlib.sha256(purpose).hexdigest()

def remaining(conn,email,secret='',*,now=None):
    initialize(conn);now=int(time.time() if now is None else now)
    # Prune only expired transient records, never accounts or sessions.
    conn.execute('DELETE FROM login_account_throttle WHERE window_started<=? AND locked_until<=?',(now-WINDOW_SECONDS,now))
    key=account_key(email,secret)
    row=conn.execute('SELECT locked_until,window_started FROM login_account_throttle WHERE account_key=?',(key,)).fetchone()
    if row is None:return 0
    if row['locked_until']>now:return int(row['locked_until'])-now
    if row['locked_until'] or row['window_started']<=now-WINDOW_SECONDS:
        # Another worker may already have begun a fresh window after our read.
        conn.execute('''DELETE FROM login_account_throttle WHERE account_key=?
            AND ((locked_until>0 AND locked_until<=?) OR window_started<=?)''',
            (key,now,now-WINDOW_SECONDS))
    return 0

def record_failure(conn,email,secret='',*,now=None):
    initialize(conn);now=int(time.time() if now is None else now)
    key=account_key(email,secret)
    # Atomic across IPs/processes. In-flight failures cannot extend an active
    # cooldown indefinitely; after expiry a new window starts at one failure.
    conn.execute('''INSERT INTO login_account_throttle(account_key,failures,window_started,locked_until)
        VALUES (?,1,?,0)
        ON CONFLICT(account_key) DO UPDATE SET
        failures=CASE
          WHEN login_account_throttle.locked_until>0 AND login_account_throttle.locked_until<=? OR login_account_throttle.window_started<=? THEN 1
          WHEN login_account_throttle.failures>=? THEN login_account_throttle.failures
          ELSE login_account_throttle.failures+1 END,
        window_started=CASE
          WHEN login_account_throttle.locked_until>0 AND login_account_throttle.locked_until<=? OR login_account_throttle.window_started<=? THEN ?
          ELSE login_account_throttle.window_started END,
        locked_until=CASE
          WHEN login_account_throttle.locked_until>? THEN login_account_throttle.locked_until
          WHEN login_account_throttle.locked_until>0 AND login_account_throttle.locked_until<=? OR login_account_throttle.window_started<=? THEN 0
          WHEN login_account_throttle.failures+1>=? THEN ? ELSE 0 END
    ''',(key,now,now,now-WINDOW_SECONDS,FAILURE_LIMIT,now,now-WINDOW_SECONDS,now,now,now,now-WINDOW_SECONDS,FAILURE_LIMIT,now+COOLDOWN_SECONDS))

def clear(conn,email,secret=''):
    initialize(conn)
    conn.execute('DELETE FROM login_account_throttle WHERE account_key=?',(account_key(email,secret),))
