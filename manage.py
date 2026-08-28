#!/usr/bin/env python
import os
import sys


def main():
    if "test" in sys.argv[1:]:
        settings_module = "pos.settings_test"
        os.environ["DJANGO_ALLOW_INSECURE_TEST_SETTINGS"] = "1"
    elif (
        os.environ.get("DJANGO_SETTINGS_MODULE") == "pos.settings_development"
        and os.environ.get("DJANGO_ALLOW_INSECURE_DEVELOPMENT", "").strip().lower()
        in {"1", "true", "si", "sí", "yes"}
    ):
        settings_module = "pos.settings_development"
    else:
        # Los comandos administrativos usan producción salvo que el modo de
        # desarrollo haya sido solicitado con las dos variables explícitas.
        settings_module = "pos.settings"
    os.environ["DJANGO_SETTINGS_MODULE"] = settings_module
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
