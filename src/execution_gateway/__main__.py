import argparse
import json
import signal

from .server import Gateway


def main():
    parser = argparse.ArgumentParser(description="Independent agent execution gateway")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    with open(args.config) as stream:
        config = json.load(stream)
    gateway = Gateway(config)

    def stop(_signum, _frame):
        gateway.stop_requested = True

    signal.signal(signal.SIGTERM, stop)
    gateway.run()


if __name__ == "__main__":
    main()
