# Cliente Android Production 1.0

El proyecto genera una sola APK para todas las sucursales. Es un contenedor WebView del POS servido por el Edge local; no incorpora SQLite de negocio, catálogo ni credenciales del VPS. Requiere Android 8.0/API 26 o posterior. La interfaz objetivo es una tableta de 8–11 pulgadas; admite ambas orientaciones y debe validarse físicamente en horizontal y vertical.

## Construcción y firma

Herramientas fijadas: JDK 17 o 21, Android SDK Platform 35, Build Tools 35.0.0, Android Gradle Plugin 8.9.2 y Gradle 8.11.1. El nombre y código de versión derivan de ../VERSION. No se genera una release sin los cuatro datos de firma externos.

En PowerShell, desde la raíz del proyecto:

    $env:ANDROID_SDK_ROOT = "C:\ruta\android-sdk"
    $env:TOCAYOS_ANDROID_KEYSTORE = "D:\custodia\tocayos-pos.jks"
    $env:TOCAYOS_ANDROID_ALIAS = "tocayos-pos"
    $env:TOCAYOS_ANDROID_STORE_PASSWORD = Read-Host "Contraseña del almacén"
    $env:TOCAYOS_ANDROID_KEY_PASSWORD = Read-Host "Contraseña de la clave"
    .\android\build.ps1
    Remove-Item Env:TOCAYOS_ANDROID_STORE_PASSWORD,Env:TOCAYOS_ANDROID_KEY_PASSWORD

android/release/ recibe la APK y SHA256SUMS.txt; ambos son artefactos de distribución, no se incorporan al código fuente. El script exige que el keystore esté fuera del repositorio y verifica el APK mediante apksigner. Para compilar con Gradle local se puede pasar -GradleExecutable C:\ruta\gradle.bat. En dos builds completos de laboratorio, las entradas ZIP y su contenido fueron idénticos, pero cambió el bloque de firma v2/v3; por ello se debe distribuir y registrar el SHA-256 del APK exacto aprobado, sin afirmar reproducibilidad binaria del APK firmado.

### Ceremonia de clave productiva

1. En una máquina de firma controlada, crear una clave nueva, distinta de la de laboratorio, con: keytool -genkeypair -keystore D:\custodia\tocayos-pos.jks -storetype JKS -alias tocayos-pos -keyalg RSA -keysize 4096 -validity 10000. Dejar que keytool solicite las contraseñas; no incluirlas en argumentos, scripts ni Git.
2. Registrar alias, algoritmo, fecha de creación y huella SHA-256 del certificado público (keytool -list -v -keystore ...). Dos custodios guardan copias cifradas independientes y verifican una restauración de la clave.
3. Firmar todas las actualizaciones con la misma clave, el mismo applicationId (mx.lostocayos.pos) y un versionCode creciente. Si se pierde la clave, Android no aceptará una actualización normal de esta APK. Una rotación debe planearse y probarse antes de la distribución; la revocación operativa exige retirar APK afectadas y redistribuir por el canal interno.
4. Registrar en el manifiesto de release la huella del certificado y el SHA-256 exacto de la APK. No usar una clave de laboratorio en Arboledas ni Santa Anita.

## Edge local y HTTPS

El técnico introduce en la tableta la URL de origen que anuncia el panel técnico, por ejemplo https://edge-arboledas.local:8443; la app rechaza HTTP, rutas, usuario, consultas y fragmentos. El certificado del Edge debe incluir ese hostname o IP en SAN, estar vigente y encadenar a una CA confiable. Para una CA interna, instalar el certificado de CA en el almacén de usuario de la tableta después de verificar su huella por un canal independiente. La configuración de red de la APK acepta CAs del sistema y del usuario, pero nunca omite la verificación TLS: un error de certificado cancela la carga. No se usa verify=False ni un callback que acepte errores SSL.

La PWA usa el mismo origen HTTPS en Chrome; el origen seguro habilita manifest y service worker. La caché PWA sólo conserva archivos estáticos, nunca HTML de sesión, ventas ni respuestas API. Si el Edge cae, muestra una pantalla de reconexión; una tableta no puede vender mientras su Edge local esté caído. La caída del VPS no afecta las ventas cuando el Edge sigue disponible.

## Instalación y actualización de laboratorio

Instalar la APK verificada con el instalador del dispositivo o adb install -r ruta\LosTocayosPOS-version.apk. La actualización conserva la URL y la identidad de terminal si se mantiene el mismo applicationId y certificado de firma. La app crea un ID tab-UUID estable en almacenamiento privado y lo comunica al frontend para impresión y bloqueo de comandas. El ID no es una credencial. La configuración se abre con Edge y Reintentar recupera la sesión al volver la LAN. Desinstalar borra la configuración; antes de sustituir una tableta, registrar su ID en el panel técnico y gestionar la identidad anterior.

Antes de promoción deben probarse en tableta física: instalación, actualización sobre versión previa, certificado válido e inválido, reinicio, pérdida y retorno de Wi‑Fi, ingreso, comanda, cobro, ruteo a ambas impresoras y ambas orientaciones. Las pruebas de laboratorio de esta entrega no sustituyen esa validación.
