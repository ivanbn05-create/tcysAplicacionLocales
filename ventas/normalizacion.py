import re
import unicodedata


def normalizar_texto(valor):
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = re.sub(r"[^a-z0-9]+", " ", texto.casefold())
    return " ".join(texto.split())


def normalizar_telefono(valor):
    digitos = re.sub(r"\D", "", str(valor or ""))
    if len(digitos) > 10 and digitos.startswith("52"):
        digitos = digitos[-10:]
    return digitos[-10:]
