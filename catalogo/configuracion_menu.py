import re
import unicodedata


TERMINOS_POR_CODIGO = {
    "TB": {"predeterminado": "dorado", "abreviaturas": {"dorado": "TD", "medio": "TM", "blando": "TB"}},
    "TBQ": {"predeterminado": "dorado", "abreviaturas": {"dorado": "TDQ", "medio": "TMQ", "blando": "TBQ"}},
    "TPLB": {"predeterminado": "blando", "abreviaturas": {"dorado": "TPLD", "medio": "TPLM", "blando": "TPLB"}},
    "TPLBQ": {"predeterminado": "blando", "abreviaturas": {"dorado": "TPLDQ", "medio": "TPLMQ", "blando": "TPLBQ"}},
    "QKH": {"predeterminado": "blando", "abreviaturas": {"dorado": "QKHD", "medio": "QKHM", "blando": "QKHB"}},
    "QKM": {"predeterminado": "blando", "abreviaturas": {"dorado": "QKMD", "medio": "QKMM", "blando": "QKMB"}},
    "TBI": {"predeterminado": "blando", "abreviaturas": {"dorado": "TKD", "medio": "TKM", "blando": "TKB"}},
    "TBIQ": {"predeterminado": "blando", "abreviaturas": {"dorado": "TKDQ", "medio": "TKMQ", "blando": "TKBQ"}},
    "TC": {"predeterminado": "blando", "abreviaturas": {"dorado": "TCHD", "medio": "TCHM", "blando": "TCHB"}},
    "TCQ": {"predeterminado": "blando", "abreviaturas": {"dorado": "TCHDQ", "medio": "TCHMQ", "blando": "TCHBQ"}},
}


ABREVIATURAS_POR_CODIGO = {
    "CO8": "CO",
    "CO05": "CO 1/2",
    "CO1": "CO 1L",
    "BBQ05": "BBQ 1/2",
    "BBQ1": "BBQ 1L",
    "LB": "LOB/SQ",
    "LOBQ": "LOBQ",
    "LBIQ": "LOKQ",
    "LOKSQ": "LOK/SQ",
    "LCQ": "LOCQ",
    "LOCSQ": "LOC/SQ",
    "GRB": "GRB",
    "GRBI": "GRK",
    "GRC": "GRCH",
    "POSTRE": "PTRS",
    "HR05": "HR 1/2",
    "HR1": "HR L",
    "HB05": "HB 1/2",
    "HB1": "HB L",
    "JAM05": "JAM 1/2",
    "JAM1": "JAM L",
    "CC": "CC",
    "CCL": "CC/L",
    "CCSA": "CC/SA",
    "FANTA": "FNTA",
    "SPRITE": "SPRT",
    "SPRITESA": "SPRT/SA",
    "SIDRAL": "SDRL",
}


ABREVIATURAS_POR_NOMBRE = {
    "CONSOME 8OZ": "CO",
    "CONSOME 1/2 LITRO": "CO 1/2",
    "CONSOME DE MEDIO LITRO": "CO 1/2",
    "CONSOME 1LT": "CO 1L",
    "CONSOME DE 1 LITRO": "CO 1L",
    "AGUA HORCHATA 1/2 ROSA": "HR 1/2",
    "AGUA DE HORCHATA ROSA DE MEDIO LITRO": "HR 1/2",
    "AGUA HORCHATA LT ROSA": "HR L",
    "H ROSA LT": "HR L",
    "AGUA DE HORCHATA ROSA DE LITRO": "HR L",
    "AGUA HORCHATA 1/2 BLANCA": "HB 1/2",
    "H BCA MEDIO": "HB 1/2",
    "AGUA DE HORCHATA BLANCA DE MEDIO LITRO": "HB 1/2",
    "AGUA HORCHATA LT BLANCA": "HB L",
    "AGUA DE HORCHATA BLANCA DE LITRO": "HB L",
    "AGUA JAMAICA 1/2": "JAM 1/2",
    "AGUA DE JAMAICA DE MEDIO LITRO": "JAM 1/2",
    "AGUA JAMAICA LT": "JAM L",
    "AGUA DE JAMAICA DE LITRO": "JAM L",
    "LONCHE BARCABOA QUESO": "LOBQ",
    "LONCHE BARBACOA QUESO": "LOBQ",
    "LONCHE DE BARBACOA CON QUESO": "LOBQ",
    "LONCHE BARBACOA": "LOB/SQ",
    "LONCHE DE BARBACOA SIN QUESO": "LOB/SQ",
    "LONCHE BISTEC QUESO": "LOKQ",
    "LONCHE DE BISTEC CON QUESO": "LOKQ",
    "LONCHE BISTEC": "LOK/SQ",
    "LONCHE DE BISTEC SIN QUESO": "LOK/SQ",
    "LONCHE DE CHORIZO CON QUESO": "LOCQ",
    "LONCHE CHORIZO": "LOC/SQ",
    "LONCHE DE CHORIZO SIN QUESO": "LOC/SQ",
    "1/2 LITRO BARBACOA": "BBQ 1/2",
    "BARBACOA DE MEDIO LITRO": "BBQ 1/2",
    "BARBACOA DE LITRO": "BBQ 1L",
    "GRINGA BARBACOA": "GRB",
    "GRINGA DE BARBACOA": "GRB",
    "GRINGA BISTEC": "GRK",
    "GRINGA DE BISTEC": "GRK",
    "GRINGA DE CHORIZO": "GRCH",
    "POSTRE": "PTRS",
    "POSTRES": "PTRS",
    "COCA": "CC",
    "COCA COLA": "CC",
    "COCA LIGHT": "CC/L",
    "COCA COLA LIGHT": "CC/L",
    "COCA S/AZUCAR": "CC/SA",
    "COCA COLA SIN AZUCAR": "CC/SA",
    "FANTA": "FNTA",
    "SPRITE": "SPRT",
    "SPRITE SIN AZUCAR": "SPRT/SA",
    "SIDRAL": "SDRL",
}


TERMINOS_POR_NOMBRE = {
    "TACO DE BARBACOA": TERMINOS_POR_CODIGO["TB"],
    "TACO DE BARBACOA C/QUESO": TERMINOS_POR_CODIGO["TBQ"],
    "TACO DE BARBACOA CON QUESO": TERMINOS_POR_CODIGO["TBQ"],
    "TACO DE BARBACOA PLANCHADO": TERMINOS_POR_CODIGO["TPLB"],
    "TACO PLANCHADO": TERMINOS_POR_CODIGO["TPLB"],
    "TACO DE BARBACOA PLANCHADO C/QUESO": TERMINOS_POR_CODIGO["TPLBQ"],
    "TACO DE BARBACOA PLANCHADO CON QUESO": TERMINOS_POR_CODIGO["TPLBQ"],
    "QUESADILLA DE HARINA": TERMINOS_POR_CODIGO["QKH"],
    "QUESADILLA DE MAIZ": TERMINOS_POR_CODIGO["QKM"],
    "TACO DE BISTEC": TERMINOS_POR_CODIGO["TBI"],
    "TACO DE BISTEC C/QUESO": TERMINOS_POR_CODIGO["TBIQ"],
    "TACO DE BISTEC CON QUESO": TERMINOS_POR_CODIGO["TBIQ"],
    "TACO DE CHORIZO": TERMINOS_POR_CODIGO["TC"],
    "TACO DE CHORIZO C/QUESO": TERMINOS_POR_CODIGO["TCQ"],
    "TACO DE CHORIZO CON QUESO": TERMINOS_POR_CODIGO["TCQ"],
}


def normalizar_nombre(nombre):
    texto = unicodedata.normalize("NFKD", str(nombre or ""))
    texto = "".join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = re.sub(r"\s+", " ", texto.upper()).strip()
    return texto


def configuracion_producto(codigo, nombre, nombre_corto=None):
    codigo = str(codigo or "").upper()
    nombre_normalizado = normalizar_nombre(nombre)
    terminos = TERMINOS_POR_CODIGO.get(codigo) or TERMINOS_POR_NOMBRE.get(nombre_normalizado)
    abreviatura = ABREVIATURAS_POR_CODIGO.get(codigo) or ABREVIATURAS_POR_NOMBRE.get(nombre_normalizado)
    if terminos:
        abreviatura = terminos["abreviaturas"][terminos["predeterminado"]]
    return {
        "nombre_corto": (abreviatura or nombre_corto or codigo)[:24],
        "permite_termino": bool(terminos),
        "termino_predeterminado": terminos["predeterminado"] if terminos else "",
        "abreviaturas_termino": terminos["abreviaturas"] if terminos else {},
    }
