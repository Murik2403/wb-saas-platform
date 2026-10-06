"""Streaming offsite envelope: RSA-OAEP-SHA256 recipient + AES-256-GCM.

Only the public recipient is needed on the server. Recovery requires the
owner's separately retained private key and then the original backup key.
"""
import argparse,base64,hashlib,json,os,re,struct,time,uuid
from contextlib import contextmanager
from pathlib import Path
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding,rsa
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes

MAGIC=b'MHOFFSITE2';CHUNK=1024*1024;MAX_HEADER=4096
OAEP=padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=b'MARKETSHELPER offsite v2')

def digest(path):
    value=hashlib.sha256();deadline=time.monotonic()+300
    with Path(path).open('rb') as source:
        while block:=source.read(CHUNK):
            if time.monotonic()>deadline:raise ValueError('offsite_hash_timeout')
            value.update(block)
    return value.hexdigest()

def recipient_id(key):
    public=key.public_key() if isinstance(key,rsa.RSAPrivateKey) else key
    return hashlib.sha256(public.public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()

def public_key(path):
    if Path(path).stat().st_size>8192:raise ValueError('offsite_public_key_invalid')
    raw=Path(path).read_bytes()
    if len(raw)>8192:raise ValueError('offsite_public_key_invalid')
    key=serialization.load_pem_public_key(raw)
    if not isinstance(key,rsa.RSAPublicKey) or not 3072<=key.key_size<=8192:raise ValueError('offsite_public_key_invalid')
    return key

@contextmanager
def atomic(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=path.with_name('.mh-offsite-'+uuid.uuid4().hex+'.partial')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:
            yield stream;stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)

def header(path):
    with Path(path).open('rb') as source:
        if source.read(len(MAGIC))!=MAGIC:raise ValueError('offsite_envelope_invalid')
        size=source.read(4)
        if len(size)!=4:raise ValueError('offsite_envelope_truncated')
        length=struct.unpack('>I',size)[0]
        if not 1<=length<=MAX_HEADER:raise ValueError('offsite_envelope_invalid')
        raw=source.read(length)
        if len(raw)!=length:raise ValueError('offsite_envelope_truncated')
        value=json.loads(raw)
        if not isinstance(value,dict) or set(value)!={'recipient','wrapped_key','nonce','source_sha256','source_bytes'}:raise ValueError('offsite_envelope_invalid')
        return value,MAGIC+size+raw

def encrypt(source,destination,recipient):
    source=Path(source);key=public_key(recipient)
    aes=os.urandom(32);nonce=os.urandom(12);checksum=digest(source)
    metadata={'recipient':recipient_id(key),'wrapped_key':base64.b64encode(key.encrypt(aes,OAEP)).decode(),
              'nonce':base64.b64encode(nonce).decode(),'source_sha256':checksum,'source_bytes':source.stat().st_size}
    raw=json.dumps(metadata,sort_keys=True,separators=(',',':')).encode()
    aad=MAGIC+struct.pack('>I',len(raw))+raw
    encryptor=Cipher(algorithms.AES(aes),modes.GCM(nonce)).encryptor();encryptor.authenticate_additional_data(aad)
    checked=hashlib.sha256();count=0;deadline=time.monotonic()+300
    with atomic(destination) as output,source.open('rb') as incoming:
        output.write(aad)
        while block:=incoming.read(CHUNK):
            if time.monotonic()>deadline:raise ValueError('offsite_encryption_timeout')
            checked.update(block);count+=len(block);output.write(encryptor.update(block))
        if checked.hexdigest()!=checksum or count!=metadata['source_bytes']:raise ValueError('offsite_source_changed')
        output.write(encryptor.finalize());output.write(encryptor.tag)
    return metadata

def decrypt(source,destination,private_key,password=None):
    source=Path(source)
    if Path(private_key).stat().st_size>16384:raise ValueError('offsite_private_key_invalid')
    raw=Path(private_key).read_bytes()
    if len(raw)>16384:raise ValueError('offsite_private_key_invalid')
    key=serialization.load_pem_private_key(raw,password=password)
    if not isinstance(key,rsa.RSAPrivateKey) or not 3072<=key.key_size<=8192:raise ValueError('offsite_private_key_invalid')
    metadata,aad=header(source)
    if metadata['recipient']!=recipient_id(key):raise ValueError('offsite_recipient_mismatch')
    try:
        wrapped=base64.b64decode(metadata['wrapped_key'],validate=True);nonce=base64.b64decode(metadata['nonce'],validate=True)
        if len(wrapped)!=key.key_size//8 or len(nonce)!=12:raise ValueError()
        aes=key.decrypt(wrapped,OAEP)
        if len(aes)!=32 or type(metadata['source_bytes']) is not int or metadata['source_bytes']<0:raise ValueError()
    except (ValueError,TypeError):raise ValueError('offsite_envelope_invalid') from None
    remaining=source.stat().st_size-len(aad)-16
    if remaining!=metadata['source_bytes']:raise ValueError('offsite_envelope_truncated')
    with source.open('rb') as incoming:incoming.seek(-16,os.SEEK_END);tag=incoming.read(16)
    decryptor=Cipher(algorithms.AES(aes),modes.GCM(nonce,tag)).decryptor();decryptor.authenticate_additional_data(aad)
    checksum=hashlib.sha256();deadline=time.monotonic()+300
    try:
        with atomic(destination) as output,source.open('rb') as incoming:
            incoming.seek(len(aad))
            while remaining:
                if time.monotonic()>deadline:raise ValueError('offsite_decryption_timeout')
                block=incoming.read(min(CHUNK,remaining))
                if not block:raise ValueError('offsite_envelope_truncated')
                remaining-=len(block);value=decryptor.update(block);checksum.update(value);output.write(value)
            tail=decryptor.finalize();checksum.update(tail);output.write(tail)
            if checksum.hexdigest()!=metadata['source_sha256']:raise ValueError('offsite_hash_mismatch')
    except InvalidTag:raise ValueError('offsite_authentication_failed') from None
    return metadata

def prepare(source_root,staging_root,recipient,groups):
    source_root,staging_root=Path(source_root).resolve(),Path(staging_root).resolve()
    if staging_root==source_root or source_root in staging_root.parents or staging_root in source_root.parents:raise ValueError('offsite_staging_overlap')
    public=public_key(recipient);identity=recipient_id(public);outputs=[]
    for group in groups:
        if not isinstance(group,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',group):raise ValueError('invalid_groups')
        directory=source_root/group
        if directory.is_symlink():raise ValueError('offsite_source_invalid')
        if (staging_root/group).is_symlink():raise ValueError('offsite_staging_invalid')
        for source in sorted(directory.glob('*.tar.gz.enc')):
            if source.is_symlink() or not source.is_file() or not re.fullmatch(r'[a-zA-Z0-9_.-]+',source.name):raise ValueError('offsite_source_invalid')
            with source.open('rb') as stream:
                if stream.read(9)!=b'WBSAASBK1':raise ValueError('encrypted_archive_format_invalid')
            checksum=digest(source)
            target=staging_root/group/(source.name[:-len('.tar.gz.enc')]+'-'+checksum+'-'+identity+'.tar.gz.enc')
            proof=target.with_suffix(target.suffix+'.sha256')
            reusable=False
            if target.is_file() and not target.is_symlink() and proof.is_file() and not proof.is_symlink():
                try:
                    info,_=header(target)
                    reusable=info['recipient']==identity and info['source_sha256']==checksum and proof.read_text()==digest(target)
                except (ValueError,OSError):pass
            if not reusable:
                encrypt(source,target,recipient)
                with atomic(proof) as output:output.write(digest(target).encode())
            outputs.append(target)
    # The caller uploads only this freshly validated set using --files-from.
    return outputs

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    enc=sub.add_parser('encrypt');enc.add_argument('source');enc.add_argument('destination');enc.add_argument('--recipient',required=True)
    dec=sub.add_parser('decrypt');dec.add_argument('source');dec.add_argument('destination');dec.add_argument('--private-key',required=True)
    args=parser.parse_args()
    if args.command=='encrypt':encrypt(args.source,args.destination,args.recipient)
    else:decrypt(args.source,args.destination,args.private_key)
