package mx.lostocayos.pos;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebStorage;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.net.http.SslError;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.util.Locale;
import java.util.UUID;

public final class MainActivity extends Activity {
    private static final String PREFS = "terminal";
    private static final String EDGE_KEY = "edge_origin";
    private static final String ID_KEY = "terminal_id";
    private SharedPreferences preferences;
    private String edgeOrigin;
    private String terminalId;
    private WebView webView;
    private LinearLayout offlinePanel;
    private TextView status;
    private TextView offlineMessage;
    private boolean navigationFailed;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        preferences = getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        terminalId = preferences.getString(ID_KEY, null);
        if (terminalId == null) {
            terminalId = "tab-" + UUID.randomUUID().toString();
            if (!preferences.edit().putString(ID_KEY, terminalId).commit()) {
                throw new IllegalStateException("No se pudo guardar la identidad de la terminal");
            }
        }
        edgeOrigin = preferences.getString(EDGE_KEY, "");
        buildScreen();
        if (edgeOrigin.isEmpty()) {
            showOffline("Configura la dirección HTTPS del Edge de esta sucursal.");
            showEdgeDialog();
        } else {
            openEdge();
        }
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void buildScreen() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setBackgroundColor(Color.WHITE);

        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.setPadding(dp(12), dp(4), dp(12), dp(4));
        toolbar.setBackgroundColor(Color.rgb(255, 237, 0));
        TextView title = new TextView(this);
        title.setText("Los Tocayos · " + terminalId);
        title.setTextColor(Color.rgb(28, 28, 28));
        title.setSingleLine(true);
        title.setTextSize(14);
        toolbar.addView(title, new LinearLayout.LayoutParams(0, dp(44), 1));
        Button configure = new Button(this);
        configure.setText("Edge");
        configure.setOnClickListener(view -> showEdgeDialog());
        toolbar.addView(configure);
        Button retry = new Button(this);
        retry.setText("Reintentar");
        retry.setOnClickListener(view -> openEdge());
        toolbar.addView(retry);
        layout.addView(toolbar);

        status = new TextView(this);
        status.setPadding(dp(12), dp(5), dp(12), dp(5));
        status.setTextSize(12);
        status.setTextColor(Color.DKGRAY);
        layout.addView(status);

        webView = new WebView(this);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSafeBrowsingEnabled(true);
        settings.setSupportMultipleWindows(false);
        webView.setWebChromeClient(new WebChromeClient());
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false);
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (!request.isForMainFrame()) return false;
                if (isSameOrigin(request.getUrl())) return false;
                Toast.makeText(MainActivity.this, "Sólo se permite el Edge configurado", Toast.LENGTH_LONG).show();
                return true;
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                navigationFailed = false;
                status.setText("Conectando con " + edgeOrigin + "…");
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (!navigationFailed) {
                    status.setText("Edge: " + edgeOrigin);
                    offlinePanel.setVisibility(View.GONE);
                    webView.setVisibility(View.VISIBLE);
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    navigationFailed = true;
                    showOffline("No se puede conectar con el Edge. Comprueba la red local y vuelve a intentar.");
                }
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
                if (request.isForMainFrame() && response.getStatusCode() >= 500) {
                    navigationFailed = true;
                    showOffline("El Edge respondió con un error. Vuelve a intentar o contacta a soporte.");
                }
            }

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handler.cancel();
                navigationFailed = true;
                showOffline("No se pudo verificar el certificado HTTPS del Edge. Revisa la URL, el certificado y la hora de la tableta.");
            }
        });
        layout.addView(webView, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));

        offlinePanel = new LinearLayout(this);
        offlinePanel.setOrientation(LinearLayout.VERTICAL);
        offlinePanel.setGravity(Gravity.CENTER);
        offlinePanel.setPadding(dp(24), dp(24), dp(24), dp(24));
        TextView heading = new TextView(this);
        heading.setText("Edge sin conexión");
        heading.setTextSize(25);
        heading.setTextColor(Color.BLACK);
        offlinePanel.addView(heading);
        offlineMessage = new TextView(this);
        offlineMessage.setTextSize(17);
        offlineMessage.setGravity(Gravity.CENTER);
        offlineMessage.setPadding(0, dp(16), 0, dp(16));
        offlinePanel.addView(offlineMessage);
        Button reconnect = new Button(this);
        reconnect.setText("Reintentar conexión");
        reconnect.setOnClickListener(view -> openEdge());
        offlinePanel.addView(reconnect);
        layout.addView(offlinePanel, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        setContentView(layout);
    }

    private void showOffline(String message) {
        status.setText("Edge: " + (edgeOrigin.isEmpty() ? "sin configurar" : edgeOrigin));
        offlineMessage.setText(message);
        webView.setVisibility(View.GONE);
        offlinePanel.setVisibility(View.VISIBLE);
    }

    private void openEdge() {
        if (edgeOrigin.isEmpty()) {
            showOffline("Configura la dirección HTTPS del Edge de esta sucursal.");
            return;
        }
        navigationFailed = false;
        status.setText("Conectando con " + edgeOrigin + "…");
        webView.loadUrl(edgeOrigin + "/tabletas/?terminal_id=" + Uri.encode(terminalId));
    }

    private void showEdgeDialog() {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setHint("https://edge.sucursal.local");
        input.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(edgeOrigin);
        int padding = dp(20);
        LinearLayout holder = new LinearLayout(this);
        holder.setPadding(padding, dp(8), padding, 0);
        holder.addView(input, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        AlertDialog dialog = new AlertDialog.Builder(this)
            .setTitle("Dirección del Edge")
            .setMessage("Introduce el origen HTTPS anunciado para esta sucursal. Terminal: " + terminalId)
            .setView(holder)
            .setNegativeButton("Cancelar", null)
            .setPositiveButton("Guardar", null)
            .create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
            String candidate = normalizeOrigin(input.getText().toString());
            if (candidate == null) {
                input.setError("Usa sólo https://host[:puerto], sin ruta, usuario ni consulta");
                return;
            }
            if (!preferences.edit().putString(EDGE_KEY, candidate).commit()) {
                input.setError("No se pudo guardar la dirección");
                return;
            }
            if (!candidate.equals(edgeOrigin)) {
                CookieManager.getInstance().removeAllCookies(null);
                WebStorage.getInstance().deleteAllData();
                webView.clearHistory();
            }
            edgeOrigin = candidate;
            dialog.dismiss();
            openEdge();
        }));
        dialog.show();
    }

    private static String normalizeOrigin(String raw) {
        String value = raw.trim();
        if (value.endsWith("/")) value = value.substring(0, value.length() - 1);
        Uri uri = Uri.parse(value);
        if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null ||
            uri.getHost().isEmpty() || uri.getUserInfo() != null ||
            (uri.getPath() != null && !uri.getPath().isEmpty()) ||
            uri.getQuery() != null || uri.getFragment() != null ||
            uri.getPort() > 65535 || uri.getPort() == 0) {
            return null;
        }
        return value;
    }

    private boolean isSameOrigin(Uri target) {
        Uri edge = Uri.parse(edgeOrigin);
        int targetPort = target.getPort() == -1 ? 443 : target.getPort();
        int edgePort = edge.getPort() == -1 ? 443 : edge.getPort();
        return "https".equalsIgnoreCase(target.getScheme()) &&
            target.getHost() != null && edge.getHost() != null &&
            target.getHost().toLowerCase(Locale.ROOT).equals(
                edge.getHost().toLowerCase(Locale.ROOT)) &&
            targetPort == edgePort;
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack() && webView.getVisibility() == View.VISIBLE) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        webView.destroy();
        super.onDestroy();
    }
}
