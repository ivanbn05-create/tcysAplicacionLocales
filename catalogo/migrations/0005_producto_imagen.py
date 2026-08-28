from django.core.validators import FileExtensionValidator
from django.db import migrations, models

import catalogo.models


class Migration(migrations.Migration):
    dependencies = [("catalogo", "0004_alter_producto_options")]

    operations = [
        migrations.AddField(
            model_name="producto",
            name="imagen",
            field=models.ImageField(
                blank=True,
                help_text="JPG, PNG o WebP estático de hasta 1 MB. El POS genera una miniatura 4:3 protegida.",
                upload_to="productos/%Y/%m/",
                validators=[
                    FileExtensionValidator(["jpg", "jpeg", "png", "webp"]),
                    catalogo.models.validar_imagen_producto,
                ],
            ),
        ),
    ]
