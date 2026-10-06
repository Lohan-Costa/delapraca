#!/opt/homebrew/bin/python3.11
import sys, random, datetime, uuid
import avb
from avb.core import AVBObject, AVBRefList
from avb.mobid import MobID as AVBMobID
PRIM = (int, float, str, bool, bytes, bytearray, type(None))

def serialize(o):
    if isinstance(o, AVBObject):
        return {'__class__': type(o).__name__, '__props__': {k: serialize(v) for k, v in o.property_data.items()}}
    if isinstance(o, AVBMobID): return {'__mobid__': o.bytes_le.hex()}
    if isinstance(o, uuid.UUID): return {'__uuid__': o.bytes_le.hex()}
    if isinstance(o, datetime.datetime): return {'__dt__': o.timestamp()}
    if isinstance(o, AVBRefList): return {'__reflist__': type(o).__name__, '__items__': [serialize(x) for x in o]}
    if isinstance(o, (list, tuple)): return {'__list__': [serialize(x) for x in o]}
    if isinstance(o, PRIM): return o
    try: return {'__attrs__': {k: serialize(v) for k, v in dict(o).items()}}
    except Exception: return {'__skip__': type(o).__name__}

def materialize(f, spec):
    if isinstance(spec, PRIM): return spec
    if isinstance(spec, dict):
        if '__mobid__' in spec: return AVBMobID(bytes_le=bytes.fromhex(spec['__mobid__']))
        if '__uuid__' in spec: return uuid.UUID(bytes_le=bytes.fromhex(spec['__uuid__']))
        if '__dt__' in spec: return datetime.datetime.fromtimestamp(spec['__dt__'])
        if '__reflist__' in spec:
            rl = getattr(f.create, spec['__reflist__'])()
            for x in spec['__items__']: rl.append(materialize(f, x))
            return rl
        if '__list__' in spec: return [materialize(f, x) for x in spec['__list__']]
        if '__attrs__' in spec:
            a = f.create.Attributes()
            for k, v in spec['__attrs__'].items(): a[k] = materialize(f, v)
            return a
        if '__skip__' in spec: return None
        if '__class__' in spec:
            cls = getattr(f.create, spec['__class__'])
            try: o = cls()
            except TypeError:
                try: o = cls(25, None)
                except TypeError: o = cls("x", "CompositionMob")
            for k, v in spec['__props__'].items(): o.property_data[k] = materialize(f, v)
            return o
    return spec

def main():
    dst, out, srcs = sys.argv[1], sys.argv[2], sys.argv[3:]
    files = [avb.open(dst)] + [avb.open(s) for s in srcs]
    try:
        fa = files[0]
        have = {m.mob_id for m in fa.content.mobs}
        added = 0
        for fb in files[1:]:
            for m in fb.content.mobs:
                if m.mob_id in have: continue
                spec = serialize(m)
                nm = materialize(fa, spec)
                fa.content.add_mob(nm)
                have.add(m.mob_id); added += 1
        fa.content.uid = random.getrandbits(63)
        fa.write(out)
        print(f"merged: +{added} mobs -> {out}")
    finally:
        for f in files: f.close()
    with avb.open(out) as f:
        ms = sorted(m.mob_id.bytes_le.hex()[-8:] for m in f.content.mobs if getattr(m, 'mob_type', '') == 'MasterMob')
        print("masters:", ms)

if __name__ == '__main__': main()
