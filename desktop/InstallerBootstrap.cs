using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("Instalador de Los Tocayos POS")]
[assembly: AssemblyDescription("Instalador del cliente Windows de Los Tocayos")]
[assembly: AssemblyCompany("Los Tocayos")]
[assembly: AssemblyProduct("Los Tocayos POS")]
[assembly: AssemblyVersion("0.2.0.0")]

namespace LosTocayos.Installer
{
    internal static class Program
    {
        private const string ResourcePrefix = "LosTocayos.Installer.Package.";

        private static readonly string[] PackageFiles = new string[]
        {
            "TocayosPOS.exe",
            "servidor.txt",
            "Instalar-LosTocayosPOS.cmd",
            "Instalar-LosTocayosPOS.ps1",
            "Configurar-Servidor.ps1",
            "Desinstalar-LosTocayosPOS.ps1",
            "LEEME.txt"
        };

        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            string temporaryDirectory = Path.Combine(
                Path.GetTempPath(),
                "LosTocayosPOS-" + Guid.NewGuid().ToString("N"));

            try
            {
                Directory.CreateDirectory(temporaryDirectory);
                ExtractPackage(temporaryDirectory);

                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = Path.Combine(temporaryDirectory, "Instalar-LosTocayosPOS.cmd");
                info.WorkingDirectory = temporaryDirectory;
                info.UseShellExecute = true;
                info.WindowStyle = ProcessWindowStyle.Normal;

                using (Process installer = Process.Start(info))
                {
                    if (installer == null)
                    {
                        throw new InvalidOperationException("Windows no pudo iniciar la instalación.");
                    }

                    installer.WaitForExit();
                }
            }
            catch (Exception exception)
            {
                MessageBox.Show(
                    "No fue posible instalar Los Tocayos POS.\n\n" + exception.Message,
                    "Instalador de Los Tocayos POS",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
            }
            finally
            {
                try
                {
                    if (Directory.Exists(temporaryDirectory))
                    {
                        Directory.Delete(temporaryDirectory, true);
                    }
                }
                catch
                {
                    // Windows limpiará la carpeta temporal posteriormente.
                }
            }
        }

        private static void ExtractPackage(string destination)
        {
            Assembly assembly = Assembly.GetExecutingAssembly();
            foreach (string fileName in PackageFiles)
            {
                string resourceName = ResourcePrefix + fileName;
                using (Stream source = assembly.GetManifestResourceStream(resourceName))
                {
                    if (source == null)
                    {
                        throw new InvalidDataException(
                            "El instalador no contiene el archivo requerido " + fileName + ".");
                    }

                    using (FileStream target = File.Create(Path.Combine(destination, fileName)))
                    {
                        source.CopyTo(target);
                    }
                }
            }
        }
    }
}
