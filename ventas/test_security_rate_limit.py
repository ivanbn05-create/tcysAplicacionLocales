from types import SimpleNamespace

from django.test import SimpleTestCase

from .auth_views import _rate_limit_keys


class SeguridadRateLimitTests(SimpleTestCase):
    def test_no_confia_en_x_forwarded_for_recibido_por_django(self):
        directo = SimpleNamespace(META={"REMOTE_ADDR": "10.20.30.40"})
        falsificado = SimpleNamespace(
            META={
                "REMOTE_ADDR": "10.20.30.40",
                "HTTP_X_FORWARDED_FOR": "203.0.113.99",
            }
        )

        self.assertEqual(
            _rate_limit_keys(directo, "operador"),
            _rate_limit_keys(falsificado, "operador"),
        )

    def test_direcciones_invalidas_comparten_un_contador_fail_closed(self):
        primera = SimpleNamespace(META={"REMOTE_ADDR": "valor-controlado-1"})
        segunda = SimpleNamespace(META={"REMOTE_ADDR": "valor-controlado-2"})

        self.assertEqual(
            _rate_limit_keys(primera, "operador"),
            _rate_limit_keys(segunda, "operador"),
        )

    def test_ipv4_mapeada_y_ipv4_comparten_contador(self):
        ipv4 = SimpleNamespace(META={"REMOTE_ADDR": "192.0.2.25"})
        mapeada = SimpleNamespace(META={"REMOTE_ADDR": "[::ffff:192.0.2.25]"})

        self.assertEqual(
            _rate_limit_keys(ipv4, "operador"),
            _rate_limit_keys(mapeada, "operador"),
        )
