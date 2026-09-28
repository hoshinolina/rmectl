import json
import pprint
import readline  # noqa: F401
import traceback

from . import device


def main():
    dev = device.RMEDigiface.open_first()
    while True:
        try:
            d = input("> ").strip()
            if not d or d.startswith("#"):
                continue
        except EOFError:
            break
        while d.endswith("\\"):
            l = input("... ").strip()
            if l.startswith("#"):
                continue
            if not l:
                continue
            d = d[:-1] + l
        v = d.strip().split(None, 1)
        if len(v) == 1:
            cmd, args = v[0], {}
        else:
            cmd, args = v
            print(args)
            args = json.loads(args)
        try:
            func = getattr(dev, cmd)
            if isinstance(args, dict):
                ret = func(**args)
            elif isinstance(args, list):
                ret = func(*args)
            else:
                ret = func(args)

            pprint.pp(ret)
        except Exception:  # noqa: BLE001
            print(traceback.format_exc())


if __name__ == "__main__":
    main()
