using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Reflection;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("Los Tocayos POS")]
[assembly: AssemblyDescription("Cliente ligero Windows para el Edge de Los Tocayos")]
[assembly: AssemblyCompany("Los Tocayos")]
[assembly: AssemblyProduct("Los Tocayos POS")]

namespace LosTocayos.Desktop
{
    internal static class Program
    {
        private const string DefaultServerUrl = "http://127.0.0.1:8000";
        private const string HealthPath = "/salud/";

        [STAThread]
        private static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            try
            {
                Options options = Options.Parse(args);
                string serverUrl = GetServerUrl();
                string terminalId = GetTerminalId();

                if (options.ShowIdentity)
                {
                    MessageBox.Show(
                        "Terminal: " + terminalId + "\nEdge: " + serverUrl,
                        "Los Tocayos POS",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Information);
                    return;
                }

                bool serviceAttempted = false;
                while (!WaitUntilAvailable(serverUrl, TimeSpan.FromSeconds(2)))
                {
                    if (options.CheckOnly)
                    {
                        Environment.ExitCode = 2;
                        return;
                    }
                    if (IsLocalServer(serverUrl) && !serviceAttempted)
                    {
                        serviceAttempted = true;
                        StartLocalService();
                        if (WaitUntilAvailable(serverUrl, TimeSpan.FromSeconds(35)))
                        {
                            break;
                        }
                    }

                    DialogResult choice = MessageBox.Show(
                        "No se puede conectar con el Edge " + serverUrl + ".\n\n" +
                        "Comprueba la red y el servicio de la sucursal. " +
                        "Puedes cambiar la dirección desde «Configurar servidor» en el menú Inicio.\n\n" +
                        "Terminal: " + terminalId + "\n\n¿Reintentar?",
                        "Los Tocayos POS",
                        MessageBoxButtons.RetryCancel,
                        MessageBoxIcon.Warning);
                    if (choice != DialogResult.Retry)
                    {
                        Environment.ExitCode = 2;
                        return;
                    }
                }

                if (options.ServerOnly)
                {
                    MessageBox.Show(
                        "El Edge de Los Tocayos está listo.",
                        "Los Tocayos POS",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Information);
                    return;
                }
                if (options.CheckOnly)
                {
                    return;
                }

                OpenDesktopWindow(serverUrl, terminalId, options.TabletMode);
            }
            catch (Exception exception)
            {
                ShowError("No fue posible iniciar Los Tocayos POS.\n\n" + exception.Message);
                Environment.ExitCode = 1;
            }
        }

        private static string UserConfigurationDirectory()
        {
            string directory = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "LosTocayosPOS");
            Directory.CreateDirectory(directory);
            return directory;
        }

        private static string GetServerUrl()
        {
            string configured = Environment.GetEnvironmentVariable("TOCAYOS_SERVER_URL");
            if (string.IsNullOrWhiteSpace(configured))
            {
                string userPath = Path.Combine(UserConfigurationDirectory(), "servidor.txt");
                string legacyPath = Path.Combine(
                    Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location), "servidor.txt");
                if (File.Exists(userPath))
                {
                    configured = File.ReadAllText(userPath);
                }
                else if (File.Exists(legacyPath))
                {
                    configured = File.ReadAllText(legacyPath);
                }
            }

            string value = string.IsNullOrWhiteSpace(configured) ? DefaultServerUrl : configured.Trim();
            value = value.TrimEnd('/');
            Uri uri;
            if (!Uri.TryCreate(value, UriKind.Absolute, out uri) ||
                (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps) ||
                !string.IsNullOrEmpty(uri.UserInfo) ||
                uri.AbsolutePath != "/" ||
                !string.IsNullOrEmpty(uri.Query) ||
                !string.IsNullOrEmpty(uri.Fragment))
            {
                throw new InvalidDataException(
                    "La dirección del Edge debe ser sólo el origen HTTP(S), sin credenciales, ruta, " +
                    "consulta ni fragmento.");
            }
            return value;
        }

        private static string GetTerminalId()
        {
            string path = Path.Combine(UserConfigurationDirectory(), "terminal-id.txt");
            if (File.Exists(path))
            {
                return ValidateTerminalId(File.ReadAllText(path).Trim());
            }

            string generated = "pc-" + Guid.NewGuid().ToString("D");
            string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            File.WriteAllText(temporary, generated);
            try
            {
                File.Move(temporary, path);
                return generated;
            }
            catch (IOException)
            {
                File.Delete(temporary);
                if (File.Exists(path))
                {
                    return ValidateTerminalId(File.ReadAllText(path).Trim());
                }
                throw;
            }
        }

        private static string ValidateTerminalId(string value)
        {
            if (!Regex.IsMatch(value, @"^pc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"))
            {
                throw new InvalidDataException(
                    "terminal-id.txt no contiene una identidad válida. Contacta a soporte antes de cambiarla.");
            }
            return value;
        }

        private static bool IsLocalServer(string serverUrl)
        {
            Uri uri;
            return Uri.TryCreate(serverUrl, UriKind.Absolute, out uri) && uri.IsLoopback;
        }

        private static bool IsAvailable(string serverUrl)
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(serverUrl + HealthPath);
                request.Method = "GET";
                request.Timeout = 1200;
                request.ReadWriteTimeout = 1200;
                request.AllowAutoRedirect = false;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                {
                    int statusCode = (int)response.StatusCode;
                    return statusCode >= 200 && statusCode < 300;
                }
            }
            catch (WebException)
            {
                return false;
            }
        }

        private static bool WaitUntilAvailable(string serverUrl, TimeSpan timeout)
        {
            Stopwatch stopwatch = Stopwatch.StartNew();
            do
            {
                if (IsAvailable(serverUrl))
                {
                    return true;
                }
                Thread.Sleep(350);
            }
            while (stopwatch.Elapsed < timeout);
            return false;
        }

        private static void StartLocalService()
        {
            string serviceController = Path.Combine(Environment.SystemDirectory, "sc.exe");
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = serviceController;
            info.Arguments = "start LosTocayosPOS";
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            using (Process process = Process.Start(info))
            {
                if (process == null)
                {
                    throw new InvalidOperationException("Windows no pudo solicitar el inicio del servicio local.");
                }
                process.StandardOutput.ReadToEnd();
                process.StandardError.ReadToEnd();
                if (!process.WaitForExit(15000))
                {
                    try { process.Kill(); } catch { }
                    throw new TimeoutException("Windows no respondió al solicitar el inicio del servicio.");
                }
            }
        }

        private static void OpenDesktopWindow(string serverUrl, string terminalId, bool tabletMode)
        {
            string edge = FindEdge();
            string route = tabletMode ? "/tabletas/" : "/";
            string profile = Path.Combine(
                UserConfigurationDirectory(), tabletMode ? "Tableta" : "Terminal");
            Directory.CreateDirectory(profile);
            string target = serverUrl + route + "?terminal_id=" + Uri.EscapeDataString(terminalId);
            string arguments =
                "--app=" + Quote(target) +
                " --user-data-dir=" + Quote(profile) +
                " --start-maximized --no-first-run --disable-session-crashed-bubble";
            if (tabletMode)
            {
                arguments += " --start-fullscreen";
            }
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = edge;
            info.Arguments = arguments;
            info.UseShellExecute = false;
            info.WorkingDirectory = Path.GetDirectoryName(edge);
            if (Process.Start(info) == null)
            {
                throw new InvalidOperationException("Microsoft Edge no pudo crear la ventana.");
            }
        }

        private static string FindEdge()
        {
            string[] candidates = new string[]
            {
                Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
                    "Microsoft", "Edge", "Application", "msedge.exe"),
                Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                    "Microsoft", "Edge", "Application", "msedge.exe")
            };
            foreach (string candidate in candidates)
            {
                if (File.Exists(candidate))
                {
                    return candidate;
                }
            }
            throw new FileNotFoundException(
                "No se encontró Microsoft Edge. Instálalo o repáralo desde Windows Update.");
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        private static void ShowError(string message)
        {
            MessageBox.Show(message, "Los Tocayos POS", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }

        private sealed class Options
        {
            public bool TabletMode { get; private set; }
            public bool ServerOnly { get; private set; }
            public bool CheckOnly { get; private set; }
            public bool ShowIdentity { get; private set; }

            public static Options Parse(string[] args)
            {
                Options options = new Options();
                foreach (string argument in args)
                {
                    if (string.Equals(argument, "--tableta", StringComparison.OrdinalIgnoreCase))
                        options.TabletMode = true;
                    else if (string.Equals(argument, "--solo-servidor", StringComparison.OrdinalIgnoreCase))
                        options.ServerOnly = true;
                    else if (string.Equals(argument, "--comprobar", StringComparison.OrdinalIgnoreCase))
                        options.CheckOnly = true;
                    else if (string.Equals(argument, "--identidad", StringComparison.OrdinalIgnoreCase))
                        options.ShowIdentity = true;
                    else
                        throw new ArgumentException("Opción desconocida: " + argument);
                }
                return options;
            }
        }
    }
}
