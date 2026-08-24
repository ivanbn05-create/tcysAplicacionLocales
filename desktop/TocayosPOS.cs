using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("Los Tocayos POS")]
[assembly: AssemblyDescription("Aplicación local de escritorio para Los Tocayos")]
[assembly: AssemblyCompany("Los Tocayos")]
[assembly: AssemblyProduct("Los Tocayos POS")]
[assembly: AssemblyVersion("0.3.0.0")]

namespace LosTocayos.Desktop
{
    internal static class Program
    {
        private const string DefaultServerUrl = "http://127.0.0.1:8000";
        private const string HealthPath = "/api/estado/";

        [STAThread]
        private static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            try
            {
                Options options = Options.Parse(args);
                string serverUrl = GetServerUrl();

                if (!WaitUntilAvailable(serverUrl, TimeSpan.FromSeconds(2)))
                {
                    if (!IsLocalServer(serverUrl))
                    {
                        ShowError(
                            "No fue posible conectar con " + serverUrl + ".\n\n" +
                            "Comprueba que el servidor de la sucursal esté encendido y que esta " +
                            "computadora se encuentre en la misma red.");
                        Environment.ExitCode = 2;
                        return;
                    }

                    StartLocalServer();
                    if (!WaitUntilAvailable(serverUrl, TimeSpan.FromSeconds(35)))
                    {
                        ShowError(
                            "El servicio local no respondió a tiempo.\n\n" +
                            "Ejecuta iniciar-local.ps1 para ver el detalle del error.");
                        Environment.ExitCode = 3;
                        return;
                    }
                }

                if (options.ServerOnly)
                {
                    MessageBox.Show(
                        "El servicio local de Los Tocayos está listo.",
                        "Los Tocayos POS",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Information);
                    return;
                }

                if (options.CheckOnly)
                {
                    return;
                }

                OpenDesktopWindow(serverUrl, options.TabletMode);
            }
            catch (Exception exception)
            {
                ShowError("No fue posible iniciar Los Tocayos POS.\n\n" + exception.Message);
                Environment.ExitCode = 1;
            }
        }

        private static string GetServerUrl()
        {
            string configured = Environment.GetEnvironmentVariable("TOCAYOS_SERVER_URL");
            if (string.IsNullOrWhiteSpace(configured))
            {
                string executableDirectory = Path.GetDirectoryName(
                    Assembly.GetExecutingAssembly().Location);
                string configurationPath = Path.Combine(executableDirectory, "servidor.txt");
                if (File.Exists(configurationPath))
                {
                    configured = File.ReadAllText(configurationPath).Trim();
                }
            }

            string value = string.IsNullOrWhiteSpace(configured) ? DefaultServerUrl : configured.Trim();
            value = value.TrimEnd('/');

            Uri uri;
            if (!Uri.TryCreate(value, UriKind.Absolute, out uri) ||
                (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
            {
                throw new InvalidDataException(
                    "La dirección configurada en servidor.txt no es válida: " + value);
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
                    return statusCode >= 200 && statusCode < 400;
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

        private static void StartLocalServer()
        {
            string projectRoot = FindProjectRoot();
            string python = Path.Combine(projectRoot, ".venv", "Scripts", "python.exe");
            string manage = Path.Combine(projectRoot, "manage.py");

            if (!File.Exists(python))
            {
                throw new FileNotFoundException(
                    "No se encontró el entorno local de Python. Ejecuta iniciar-local.ps1 una vez " +
                    "para completar la instalación.",
                    python);
            }

            RunSetupStep(python, projectRoot, "\"" + manage + "\" migrate --noinput", "migraciones");

            ProcessStartInfo info = CreatePythonStartInfo(
                python,
                projectRoot,
                "\"" + manage + "\" runserver 0.0.0.0:8000 --noreload");
            info.UseShellExecute = false;
            info.CreateNoWindow = true;

            Process process = Process.Start(info);
            if (process == null)
            {
                throw new InvalidOperationException("Windows no pudo crear el servicio local.");
            }
        }

        private static void RunSetupStep(
            string python,
            string projectRoot,
            string arguments,
            string description)
        {
            ProcessStartInfo info = CreatePythonStartInfo(python, projectRoot, arguments);
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;

            using (Process process = Process.Start(info))
            {
                if (process == null)
                {
                    throw new InvalidOperationException("No fue posible ejecutar " + description + ".");
                }

                string output = process.StandardOutput.ReadToEnd();
                string error = process.StandardError.ReadToEnd();
                if (!process.WaitForExit(120000))
                {
                    try
                    {
                        process.Kill();
                    }
                    catch
                    {
                        // El proceso ya había finalizado.
                    }

                    throw new TimeoutException("La ejecución de " + description + " excedió dos minutos.");
                }

                if (process.ExitCode != 0)
                {
                    string detail = string.IsNullOrWhiteSpace(error) ? output : error;
                    throw new InvalidOperationException(
                        "Falló la ejecución de " + description + ".\n\n" + detail.Trim());
                }
            }
        }

        private static ProcessStartInfo CreatePythonStartInfo(
            string python,
            string projectRoot,
            string arguments)
        {
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = python;
            info.WorkingDirectory = projectRoot;
            info.Arguments = arguments;
            info.EnvironmentVariables["DJANGO_ALLOWED_HOSTS"] = "*";
            info.EnvironmentVariables["DJANGO_DEBUG"] = "true";
            info.EnvironmentVariables["PRINT_BACKEND"] = GetEnvironmentOrDefault("PRINT_BACKEND", "tcp");
            info.EnvironmentVariables["PRINT_SYNC"] = GetEnvironmentOrDefault("PRINT_SYNC", "true");
            return info;
        }

        private static string GetEnvironmentOrDefault(string name, string defaultValue)
        {
            string value = Environment.GetEnvironmentVariable(name);
            return string.IsNullOrWhiteSpace(value) ? defaultValue : value;
        }

        private static string FindProjectRoot()
        {
            string location = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            DirectoryInfo directory = new DirectoryInfo(location);

            while (directory != null)
            {
                if (File.Exists(Path.Combine(directory.FullName, "manage.py")))
                {
                    return directory.FullName;
                }

                directory = directory.Parent;
            }

            string configured = Environment.GetEnvironmentVariable("TOCAYOS_APP_ROOT");
            if (!string.IsNullOrWhiteSpace(configured) &&
                File.Exists(Path.Combine(configured, "manage.py")))
            {
                return Path.GetFullPath(configured);
            }

            throw new DirectoryNotFoundException(
                "No se encontró manage.py. Conserva el ejecutable dentro de la carpeta del " +
                "proyecto o define TOCAYOS_APP_ROOT.");
        }

        private static void OpenDesktopWindow(string serverUrl, bool tabletMode)
        {
            string edge = FindEdge();
            string route = tabletMode ? "/tabletas/" : "/";
            string profile = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "LosTocayosPOS",
                tabletMode ? "Tableta" : "Terminal");
            Directory.CreateDirectory(profile);

            string arguments =
                "--app=" + Quote(serverUrl + route) +
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
            MessageBox.Show(
                message,
                "Los Tocayos POS",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
        }

        private sealed class Options
        {
            public bool TabletMode { get; private set; }
            public bool ServerOnly { get; private set; }
            public bool CheckOnly { get; private set; }

            public static Options Parse(string[] args)
            {
                Options options = new Options();
                foreach (string argument in args)
                {
                    if (string.Equals(argument, "--tableta", StringComparison.OrdinalIgnoreCase))
                    {
                        options.TabletMode = true;
                    }
                    else if (string.Equals(argument, "--solo-servidor", StringComparison.OrdinalIgnoreCase))
                    {
                        options.ServerOnly = true;
                    }
                    else if (string.Equals(argument, "--comprobar", StringComparison.OrdinalIgnoreCase))
                    {
                        options.CheckOnly = true;
                    }
                }

                return options;
            }
        }
    }
}
