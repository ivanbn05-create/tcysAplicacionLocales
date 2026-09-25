using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("Instalador de Los Tocayos POS")]
[assembly: AssemblyDescription("Instalador verificado del cliente Windows de Los Tocayos")]
[assembly: AssemblyCompany("Los Tocayos")]
[assembly: AssemblyProduct("Los Tocayos POS")]

namespace LosTocayos.Installer
{
    internal static class Program
    {
        private const string ResourcePrefix = "LosTocayos.Installer.Package.";
        private const string Product = "LosTocayosPOS-Cliente-Windows";
        private const string Algorithm = "RSA-3072-SHA256-PKCS1v15";
        private const string ZipName = "LosTocayosPOS-Cliente-Windows.zip";
        private const string ManifestName = "client-manifest.json";
        private static readonly string[] PackageFiles = new string[]
        {
            "TocayosPOS.exe",
            "Instalar-LosTocayosPOS.cmd",
            "Instalar-LosTocayosPOS.ps1",
            "Configurar-Servidor.ps1",
            "Desinstalar-LosTocayosPOS.ps1",
            "LEEME.txt"
        };

        [STAThread]
        private static int Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            bool verifyOnly = args.Length == 1 &&
                string.Equals(args[0], "--verificar", StringComparison.OrdinalIgnoreCase);
            string temporaryDirectory = Path.Combine(
                Path.GetTempPath(), "LosTocayosPOS-" + Guid.NewGuid().ToString("N"));
            try
            {
                if (args.Length > 0 && !verifyOnly)
                    throw new ArgumentException("Opción desconocida.");
                byte[] archive = ReadResource("payload.zip", 33554432);
                byte[] manifest = ReadResource(ManifestName, 1048576);
                byte[] checksum = ReadResource("SHA256SUMS.txt", 8192);
                byte[] signature = ReadResource("release-signature.json", 16384);
                VerifyRelease(archive, manifest, checksum, signature, GetTrustStorePath());
                Directory.CreateDirectory(temporaryDirectory);
                ExtractVerifiedPackage(archive, manifest, temporaryDirectory);
                if (verifyOnly)
                    return 0;

                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = Path.Combine(temporaryDirectory, "Instalar-LosTocayosPOS.cmd");
                info.WorkingDirectory = temporaryDirectory;
                info.UseShellExecute = true;
                info.WindowStyle = ProcessWindowStyle.Normal;
                using (Process installer = Process.Start(info))
                {
                    if (installer == null)
                        throw new InvalidOperationException("Windows no pudo iniciar la instalación.");
                    installer.WaitForExit();
                    if (installer.ExitCode != 0)
                    {
                        MessageBox.Show(
                            "La instalación no se completó. Código: " + installer.ExitCode + ".",
                            "Instalador de Los Tocayos POS",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Error);
                        return installer.ExitCode;
                    }
                }
                return 0;
            }
            catch (Exception exception)
            {
                if (verifyOnly)
                {
                    Console.Error.WriteLine(exception.Message);
                    return 1;
                }
                MessageBox.Show(
                    "No fue posible instalar Los Tocayos POS.\n\n" + exception.Message,
                    "Instalador de Los Tocayos POS",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return 1;
            }
            finally
            {
                try
                {
                    if (Directory.Exists(temporaryDirectory))
                        Directory.Delete(temporaryDirectory, true);
                }
                catch
                {
                    // Windows limpiará la carpeta temporal posteriormente.
                }
            }
        }

        private static string GetTrustStorePath()
        {
            string configured = Environment.GetEnvironmentVariable("TOCAYOS_RELEASE_TRUST");
            string path = !string.IsNullOrWhiteSpace(configured) ? configured :
                Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
                    "LosTocayosPOS", "release-trust.json");
            if (!Path.IsPathRooted(path) || !File.Exists(path) ||
                (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            {
                throw new InvalidDataException(
                    "Falta el trust store externo. Configura TOCAYOS_RELEASE_TRUST con su ruta absoluta.");
            }
            return path;
        }

        private static byte[] ReadResource(string name, int maxBytes)
        {
            using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(
                ResourcePrefix + name))
            {
                if (stream == null || stream.Length > maxBytes)
                    throw new InvalidDataException("Recurso de release faltante o demasiado grande.");
                using (MemoryStream memory = new MemoryStream())
                {
                    stream.CopyTo(memory);
                    return memory.ToArray();
                }
            }
        }

        private static byte[] ReadBoundedFile(string path, int maxBytes)
        {
            FileInfo file = new FileInfo(path);
            if (file.Length > maxBytes)
                throw new InvalidDataException("Trust store demasiado grande.");
            return File.ReadAllBytes(path);
        }

        private static Dictionary<string, object> JsonObject(byte[] bytes)
        {
            JavaScriptSerializer parser = new JavaScriptSerializer();
            parser.MaxJsonLength = 1048576;
            Dictionary<string, object> result = parser.DeserializeObject(
                Encoding.UTF8.GetString(bytes)) as Dictionary<string, object>;
            if (result == null)
                throw new InvalidDataException("JSON de release inválido.");
            return result;
        }

        private static string Field(Dictionary<string, object> source, string name)
        {
            object value;
            if (!source.TryGetValue(name, out value) || !(value is string))
                throw new InvalidDataException("Campo de release faltante: " + name);
            return (string)value;
        }

        private static long Number(Dictionary<string, object> source, string name)
        {
            object value;
            if (!source.TryGetValue(name, out value) || value == null)
                throw new InvalidDataException("Campo numérico faltante: " + name);
            return Convert.ToInt64(value);
        }

        private static object[] ArrayField(Dictionary<string, object> source, string name)
        {
            object value;
            if (!source.TryGetValue(name, out value) || !(value is object[]))
                throw new InvalidDataException("Lista de release faltante: " + name);
            return (object[])value;
        }

        private static string Hash(byte[] bytes)
        {
            using (SHA256 sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private static void RequireHash(string value)
        {
            if (!Regex.IsMatch(value, "^[0-9a-f]{64}$"))
                throw new InvalidDataException("SHA-256 inválido en release.");
        }

        private static void VerifyRelease(
            byte[] archive, byte[] manifestBytes, byte[] checksumBytes,
            byte[] signatureBytes, string trustPath)
        {
            Dictionary<string, object> manifest = JsonObject(manifestBytes);
            Dictionary<string, object> signature = JsonObject(signatureBytes);
            Dictionary<string, object> trust = JsonObject(ReadBoundedFile(trustPath, 65536));
            string version = Field(manifest, "release_version");
            if (Number(manifest, "schema_version") != 1 ||
                Field(manifest, "product") != Product ||
                !Regex.IsMatch(version, "^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$") ||
                Number(signature, "schema_version") != 1 ||
                Field(signature, "algorithm") != Algorithm ||
                Field(signature, "product") != Product ||
                Field(signature, "version") != version ||
                Number(trust, "schema_version") != 1)
                throw new InvalidDataException("Contrato de release desconocido.");

            string archiveHash = Hash(archive);
            string manifestHash = Hash(manifestBytes);
            string checksumHash = Hash(checksumBytes);
            if (Field(signature, "archive_sha256") != archiveHash ||
                Field(signature, "manifest_sha256") != manifestHash ||
                Field(signature, "checksum_sha256") != checksumHash)
                throw new InvalidDataException("Los hashes del paquete no coinciden con la firma.");

            string checksums = Encoding.UTF8.GetString(checksumBytes).Replace("\r\n", "\n");
            string expectedChecksums = archiveHash + "  " + ZipName + "\n" +
                manifestHash + "  " + ManifestName + "\n";
            if (checksums != expectedChecksums)
                throw new InvalidDataException("SHA256SUMS no coincide con el paquete.");

            string keyId = Field(signature, "key_id");
            RequireHash(keyId);
            string publicXml = null;
            foreach (object entry in ArrayField(trust, "keys"))
            {
                Dictionary<string, object> key = entry as Dictionary<string, object>;
                if (key != null && Field(key, "key_id") == keyId &&
                    Field(key, "status") == "trusted")
                {
                    if (publicXml != null)
                        throw new InvalidDataException("Clave de firma duplicada.");
                    publicXml = Field(key, "public_xml");
                }
            }
            if (publicXml == null || Hash(Encoding.UTF8.GetBytes(publicXml)) != keyId)
                throw new InvalidDataException("Publicador desconocido o revocado.");

            string message = string.Join("\n", new string[]
            {
                "LosTocayosPOS-Release-Signature-v1", Product, version, keyId,
                archiveHash, manifestHash, checksumHash, ""
            });
            byte[] rawSignature;
            try { rawSignature = Convert.FromBase64String(Field(signature, "signature_base64")); }
            catch (FormatException) { throw new InvalidDataException("Firma base64 inválida."); }
            CspParameters parameters = new CspParameters(24);
            using (RSACryptoServiceProvider rsa = new RSACryptoServiceProvider(3072, parameters))
            {
                rsa.FromXmlString(publicXml);
                if (rsa.KeySize < 3072 ||
                    !rsa.VerifyData(Encoding.UTF8.GetBytes(message),
                        CryptoConfig.MapNameToOID("SHA256"), rawSignature))
                    throw new InvalidDataException("Firma RSA inválida.");
            }
        }

        private static void ExtractVerifiedPackage(
            byte[] archiveBytes, byte[] manifestBytes, string destination)
        {
            Dictionary<string, object> manifest = JsonObject(manifestBytes);
            Dictionary<string, Dictionary<string, object>> listed =
                new Dictionary<string, Dictionary<string, object>>(StringComparer.Ordinal);
            foreach (object item in ArrayField(manifest, "files"))
            {
                Dictionary<string, object> file = item as Dictionary<string, object>;
                if (file == null)
                    throw new InvalidDataException("Entrada de manifiesto inválida.");
                string name = Field(file, "path");
                string hash = Field(file, "sha256");
                RequireHash(hash);
                if (name != Path.GetFileName(name) || name.Contains("/") ||
                    name.Contains("\\") || listed.ContainsKey(name))
                    throw new InvalidDataException("Ruta de paquete inválida o duplicada.");
                listed.Add(name, file);
            }
            if (listed.Count != PackageFiles.Length)
                throw new InvalidDataException("Cantidad de archivos de paquete inesperada.");
            foreach (string required in PackageFiles)
            {
                if (!listed.ContainsKey(required))
                    throw new InvalidDataException("Falta archivo de paquete requerido.");
            }

            HashSet<string> seen = new HashSet<string>(StringComparer.Ordinal);
            using (MemoryStream input = new MemoryStream(archiveBytes))
            using (ZipArchive archive = new ZipArchive(input, ZipArchiveMode.Read))
            {
                if (archive.Entries.Count != PackageFiles.Length)
                    throw new InvalidDataException("ZIP contiene entradas inesperadas.");
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    Dictionary<string, object> expected;
                    if (!listed.TryGetValue(entry.FullName, out expected) ||
                        !seen.Add(entry.FullName) ||
                        entry.Length > 8388608 ||
                        entry.Length != Number(expected, "size"))
                        throw new InvalidDataException("Archivo ZIP inesperado o demasiado grande.");
                    byte[] contents;
                    using (Stream data = entry.Open())
                    using (MemoryStream memory = new MemoryStream())
                    {
                        data.CopyTo(memory);
                        contents = memory.ToArray();
                    }
                    if (contents.LongLength != entry.Length ||
                        Hash(contents) != Field(expected, "sha256"))
                        throw new InvalidDataException("Archivo del paquete alterado.");
                    File.WriteAllBytes(Path.Combine(destination, entry.FullName), contents);
                }
            }
        }
    }
}
