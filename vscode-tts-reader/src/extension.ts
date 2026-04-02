import * as vscode from "vscode";
import * as cp from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as net from "net";
import { BridgeClient } from "./bridgeClient";
import { Highlighter } from "./highlighter";

// ---------------------------------------------------------------------------
// グローバル状態
// ---------------------------------------------------------------------------

let client: BridgeClient | null = null;
let highlighter: Highlighter | null = null;
let serverProcess: cp.ChildProcess | null = null;
let statusBarItem: vscode.StatusBarItem;
let outputChannel: vscode.OutputChannel;
let isReading = false;

// ---------------------------------------------------------------------------
// Activate / Deactivate
// ---------------------------------------------------------------------------

export async function activate(
  context: vscode.ExtensionContext
): Promise<void> {
  outputChannel = vscode.window.createOutputChannel("TTS Reader");
  log("Activating TTS Reader extension...");

  // ステータスバー
  statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100
  );
  updateStatusBar("idle");
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  // ハイライター
  highlighter = new Highlighter();
  context.subscriptions.push({ dispose: () => highlighter?.dispose() });

  // コマンド登録
  context.subscriptions.push(
    vscode.commands.registerCommand("ttsReader.speakDocument", cmdSpeakDocument),
    vscode.commands.registerCommand("ttsReader.speakSelection", cmdSpeakSelection),
    vscode.commands.registerCommand("ttsReader.stop", cmdStop),
    vscode.commands.registerCommand("ttsReader.configure", cmdConfigure),
    vscode.commands.registerCommand("ttsReader.restartServer", cmdRestartServer)
  );

  // 設定変更の監視
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("ttsReader")) {
        syncConfiguration();
      }
    })
  );

  // エディタ切り替え時にハイライトをクリア
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(() => {
      if (highlighter && !isReading) {
        const editor = vscode.window.activeTextEditor;
        if (editor) {
          highlighter.clear(editor);
        }
      }
    })
  );

  // 自動起動
  const config = vscode.workspace.getConfiguration("ttsReader");
  if (config.get<boolean>("autoStartServer", true)) {
    await startPipeline(context);
  }

  log("TTS Reader extension activated.");
}

export async function deactivate(): Promise<void> {
  log("Deactivating TTS Reader extension...");
  disconnectClient();
  stopServer();
  log("TTS Reader extension deactivated.");
}

// ---------------------------------------------------------------------------
// Python 環境管理
// ---------------------------------------------------------------------------

function getPythonDir(context?: vscode.ExtensionContext): string {
  // 拡張機能に同梱された python/ ディレクトリ
  // 開発時は tts-reader プロジェクトルートを直接参照する設定も可能
  const config = vscode.workspace.getConfiguration("ttsReader");
  const custom = config.get<string>("pythonProjectPath", "");
  if (custom) {
    return custom;
  }
  return path.join(__dirname, "..", "python");
}

function getVenvDir(pythonDir: string): string {
  return path.join(pythonDir, ".venv");
}

function getVenvPython(pythonDir: string): string {
  const venvDir = getVenvDir(pythonDir);
  // Windows
  const winPython = path.join(venvDir, "Scripts", "python.exe");
  if (fs.existsSync(winPython)) {
    return winPython;
  }
  // Linux / macOS
  const unixPython = path.join(venvDir, "bin", "python");
  if (fs.existsSync(unixPython)) {
    return unixPython;
  }
  // venv が未作成の場合はシステムの python を返す
  const config = vscode.workspace.getConfiguration("ttsReader");
  return config.get<string>("pythonPath", "python");
}

/**
 * uvコマンドが利用可能かチェック
 */
async function detectUv(): Promise<string | null> {
  // 1. PATHにuvがあるか
  for (const cmd of ["uv", "uv.exe"]) {
    try {
      await execAsync(`"${cmd}" --version`);
      return cmd;
    } catch {
      // not found
    }
  }

  // 2. 既知のインストール先を探す (cargo install uv / pipx install uv)
  const homeDir = process.env.HOME || process.env.USERPROFILE || "";
  const candidates = [
    path.join(homeDir, ".cargo", "bin", process.platform === "win32" ? "uv.exe" : "uv"),
    path.join(homeDir, ".local", "bin", "uv"),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) {
      return p;
    }
  }

  return null;
}

/**
 * Python環境をセットアップ（uv優先、pip fallback）
 */
async function ensurePythonEnv(pythonDir: string): Promise<boolean> {
  const venvDir = getVenvDir(pythonDir);

  if (fs.existsSync(venvDir)) {
    log("Python venv already exists.");
    return true;
  }

  return vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: "TTS Reader",
      cancellable: false,
    },
    async (progress) => {
      try {
        const uvCmd = await detectUv();

        if (uvCmd) {
          // ========= uv ルート =========
          log(`Using uv: ${uvCmd}`);
          progress.report({ message: "Setting up environment with uv..." });

          // uv sync は pyproject.toml + uv.lock から
          // venv作成 → 依存解決 → インストール を一括実行
          await execAsync(`"${uvCmd}" sync`, pythonDir);

          log("uv sync completed successfully.");
        } else {
          // ========= pip フォールバック =========
          log("uv not found. Falling back to pip.");

          // venv作成
          progress.report({ message: "Creating virtual environment..." });
          const config = vscode.workspace.getConfiguration("ttsReader");
          const systemPython = config.get<string>("pythonPath", "python");
          await execAsync(`"${systemPython}" -m venv "${venvDir}"`);

          // pip upgrade
          progress.report({ message: "Upgrading pip..." });
          const venvPython = getVenvPython(pythonDir);
          await execAsync(`"${venvPython}" -m pip install --upgrade pip`);

          // 依存インストール
          progress.report({ message: "Installing dependencies..." });
          const reqFile = path.join(pythonDir, "requirements.txt");
          if (fs.existsSync(reqFile)) {
            await execAsync(
              `"${venvPython}" -m pip install -r "${reqFile}"`
            );
          }

          // tts_reader 自身のインストール
          progress.report({ message: "Installing tts-reader..." });
          const pyprojectFile = path.join(pythonDir, "pyproject.toml");
          if (fs.existsSync(pyprojectFile)) {
            await execAsync(
              `"${venvPython}" -m pip install -e "${pythonDir}"`
            );
          }
        }

        log("Python environment setup complete.");
        vscode.window.showInformationMessage(
          "TTS Reader: Python environment ready."
        );
        return true;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        log(`Python environment setup failed: ${msg}`);

        // 中途半端なvenvを削除
        if (fs.existsSync(venvDir)) {
          try {
            fs.rmSync(venvDir, { recursive: true, force: true });
            log("Cleaned up partial venv.");
          } catch {
            log("Failed to clean up partial venv.");
          }
        }

        vscode.window.showErrorMessage(
          "TTS Reader: Failed to set up Python environment. " +
          "Check Output panel (TTS Reader) for details."
        );
        return false;
      }
    }
  );
}

function getPip(pythonDir: string): string {
  const venvDir = getVenvDir(pythonDir);
  const winPip = path.join(venvDir, "Scripts", "pip.exe");
  if (fs.existsSync(winPip)) {
    return winPip;
  }
  return path.join(venvDir, "bin", "pip");
}

// ---------------------------------------------------------------------------
// ブリッジサーバ管理
// ---------------------------------------------------------------------------

async function startPipeline(
  context: vscode.ExtensionContext
): Promise<void> {
  const pythonDir = getPythonDir(context);

  if (!fs.existsSync(pythonDir)) {
    vscode.window.showWarningMessage(
      "TTS Reader: Python project not found. Please set ttsReader.pythonProjectPath."
    );
    return;
  }

  // Python 環境のセットアップ
  const envReady = await ensurePythonEnv(pythonDir);
  if (!envReady) {
    return;
  }

  // ブリッジサーバ起動
  await startBridgeServer(pythonDir);

  // クライアント接続
  await connectClient();
}

async function startBridgeServer(pythonDir: string): Promise<void> {
  if (serverProcess) {
    log("Bridge server already running.");
    return;
  }

  const config = vscode.workspace.getConfiguration("ttsReader");
  const host = config.get<string>("bridgeHost", "127.0.0.1");
  const port = config.get<number>("bridgePort", 9120);
  const ttsUrl = config.get<string>("ttsServerUrl", "http://localhost:8000");

  // ポートが使用中か確認
  const portInUse = await isPortInUse(host, port);
  if (portInUse) {
    log(`Port ${port} already in use. Attempting to connect to existing server.`);
    return;
  }

  const pythonExe = getVenvPython(pythonDir);
  const args = [
    "-m",
    "tts_reader.adapters.vscode_adapter",
    "--host", host,
    "--port", port.toString(),
    "--tts-url", ttsUrl,
    "--log-level", "INFO",
  ];

  log(`Starting bridge server: ${pythonExe} ${args.join(" ")}`);

  serverProcess = cp.spawn(pythonExe, args, {
    cwd: path.join(pythonDir, "src"),
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env },
  });

  serverProcess.stdout?.on("data", (data: Buffer) => {
    log(`[server] ${data.toString().trim()}`);
  });

  serverProcess.stderr?.on("data", (data: Buffer) => {
    log(`[server:err] ${data.toString().trim()}`);
  });

  serverProcess.on("error", (err: Error) => {
    log(`Bridge server failed to start: ${err.message}`);
    vscode.window.showErrorMessage(
      `TTS Reader: Bridge server failed to start. ${err.message}`
    );
    serverProcess = null;
    updateStatusBar("error");
  });

  serverProcess.on("exit", (code, signal) => {
    log(`Bridge server exited (code=${code}, signal=${signal})`);
    serverProcess = null;
    if (code !== 0 && code !== null) {
      updateStatusBar("error");
    }
  });

  // サーバが起動するまで待機（ポーリング）
  const ready = await waitForServer(host, port, 10000);
  if (!ready) {
    vscode.window.showWarningMessage(
      "TTS Reader: Bridge server did not start in time."
    );
    updateStatusBar("error");
  }
}

function stopServer(): void {
  if (serverProcess) {
    log("Stopping bridge server...");
    serverProcess.kill("SIGTERM");
    // Windows では SIGTERM が効かない場合がある
    setTimeout(() => {
      if (serverProcess && !serverProcess.killed) {
        serverProcess.kill("SIGKILL");
      }
      serverProcess = null;
    }, 3000);
  }
}

async function waitForServer(
  host: string,
  port: number,
  timeoutMs: number
): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await isPortInUse(host, port)) {
      log("Bridge server is ready.");
      return true;
    }
    await sleep(500);
  }
  return false;
}

function isPortInUse(host: string, port: number): Promise<boolean> {
  return new Promise((resolve) => {
    // WebSocket サーバに対して生TCP接続するとハンドシェイクエラーが出るため、
    // 短命なWebSocket接続で確認する
    const testWs = new (require("ws") as typeof import("ws"))(
      `ws://${host}:${port}`
    );
    const timer = setTimeout(() => {
      testWs.terminate();
      resolve(false);
    }, 1000);

    testWs.on("open", () => {
      clearTimeout(timer);
      testWs.close();
      resolve(true);
    });
    testWs.on("error", () => {
      clearTimeout(timer);
      resolve(false);
    });
  });
}

// ---------------------------------------------------------------------------
// WebSocket クライアント管理
// ---------------------------------------------------------------------------

async function connectClient(): Promise<void> {
  disconnectClient();

  const config = vscode.workspace.getConfiguration("ttsReader");
  const host = config.get<string>("bridgeHost", "127.0.0.1");
  const port = config.get<number>("bridgePort", 9120);

  client = new BridgeClient(host, port);

  client.setOnConnected(() => {
    log("Connected to bridge server.");
    updateStatusBar("idle");
    syncConfiguration();
  });

  client.setOnDisconnected(() => {
    log("Disconnected from bridge server.");
    updateStatusBar("disconnected");
    isReading = false;
  });

  client.setOnPlayback((data) => {
    const editor = vscode.window.activeTextEditor;
    if (editor && highlighter) {
      highlighter.update(editor, data);
    }
    if (data.state === "playing" && data.chunk.type === "text") {
      isReading = true;
      updateStatusBar("reading");
    }
  });

  client.setOnDone(() => {
    isReading = false;
    updateStatusBar("idle");
    const editor = vscode.window.activeTextEditor;
    if (editor && highlighter) {
      highlighter.clear(editor);
    }
    vscode.window.setStatusBarMessage("TTS Reader: Reading complete.", 3000);
  });

  client.setOnError((msg) => {
    log(`Bridge error: ${msg}`);
    vscode.window.showErrorMessage(`TTS Reader: ${msg}`);
    isReading = false;
    updateStatusBar("idle");
  });

  try {
    await client.connect();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log(`Connection failed: ${msg}`);
    vscode.window.showWarningMessage(
      "TTS Reader: Could not connect to bridge server. Is it running?"
    );
    updateStatusBar("disconnected");
  }
}

function disconnectClient(): void {
  if (client) {
    client.disconnect();
    client = null;
  }
}

// ---------------------------------------------------------------------------
// コマンド
// ---------------------------------------------------------------------------

async function cmdSpeakDocument(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showInformationMessage("TTS Reader: No active editor.");
    return;
  }

  if (!client?.isConnected) {
    const action = await vscode.window.showWarningMessage(
      "TTS Reader: Not connected to bridge server.",
      "Start Server",
      "Cancel"
    );
    if (action === "Start Server") {
      await cmdRestartServer();
    }
    return;
  }

  if (isReading) {
    // 読み上げ中に再度呼ばれたら停止してから再開
    client.stop();
    await sleep(200);
  }

  const text = editor.document.getText();
  if (!text.trim()) {
    vscode.window.showInformationMessage("TTS Reader: Document is empty.");
    return;
  }

  log(`Speaking document: ${editor.document.fileName}`);
  client.speak(text);
}

async function cmdSpeakSelection(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showInformationMessage("TTS Reader: No active editor.");
    return;
  }

  if (!client?.isConnected) {
    const action = await vscode.window.showWarningMessage(
      "TTS Reader: Not connected to bridge server.",
      "Start Server",
      "Cancel"
    );
    if (action === "Start Server") {
      await cmdRestartServer();
    }
    return;
  }

  const selection = editor.selection;
  if (selection.isEmpty) {
    vscode.window.showInformationMessage("TTS Reader: No text selected.");
    return;
  }

  if (isReading) {
    client.stop();
    await sleep(200);
  }

  const text = editor.document.getText(selection);
  log(`Speaking selection (${text.length} chars)`);
  client.speak(text);
}

function cmdStop(): void {
  if (client?.isConnected) {
    client.stop();
  }
  isReading = false;
  updateStatusBar("idle");
  const editor = vscode.window.activeTextEditor;
  if (editor && highlighter) {
    highlighter.clear(editor);
  }
  log("Stopped reading.");
}

async function cmdConfigure(): Promise<void> {
  const config = vscode.workspace.getConfiguration("ttsReader");

  const pick = await vscode.window.showQuickPick(
    [
      { label: "Caption", description: "Voice direction caption for TTS" },
      { label: "Seed", description: "Seed for reproducible generation" },
      { label: "Voice", description: "TTS voice name" },
      { label: "Speed", description: "Playback speed" },
      { label: "TTS Server URL", description: "Local TTS server address" },
    ],
    { placeHolder: "Select a setting to configure" }
  );

  if (!pick) {
    return;
  }

  switch (pick.label) {
    case "Caption": {
      const value = await vscode.window.showInputBox({
        prompt: "Voice direction caption (empty to clear)",
        placeHolder: "e.g. Speak slowly and clearly in a warm tone",
        value: config.get<string>("caption", ""),
      });
      if (value !== undefined) {
        await config.update("caption", value, vscode.ConfigurationTarget.Global);
        client?.configure({ caption: value });
      }
      break;
    }
    case "Seed": {
      const current = config.get<number | null>("seed", null);
      const value = await vscode.window.showInputBox({
        prompt: "Seed value (empty to clear)",
        placeHolder: "e.g. 42",
        value: current !== null ? String(current) : "",
      });
      if (value !== undefined) {
        const seed = value === "" ? null : parseInt(value, 10);
        if (value !== "" && isNaN(seed as number)) {
          vscode.window.showWarningMessage("TTS Reader: Seed must be a number.");
          return;
        }
        await config.update("seed", seed, vscode.ConfigurationTarget.Global);
        client?.configure({ seed });
      }
      break;
    }
    case "Voice": {
      const value = await vscode.window.showInputBox({
        prompt: "Voice name",
        placeHolder: "e.g. alloy, nova, shimmer",
        value: config.get<string>("voice", "alloy"),
      });
      if (value !== undefined) {
        await config.update("voice", value, vscode.ConfigurationTarget.Global);
        client?.configure({ voice: value });
      }
      break;
    }
    case "Speed": {
      const value = await vscode.window.showInputBox({
        prompt: "Playback speed (0.5 - 2.0)",
        placeHolder: "e.g. 1.0",
        value: String(config.get<number>("speed", 1.0)),
      });
      if (value !== undefined) {
        const speed = parseFloat(value);
        if (isNaN(speed) || speed < 0.5 || speed > 2.0) {
          vscode.window.showWarningMessage(
            "TTS Reader: Speed must be between 0.5 and 2.0."
          );
          return;
        }
        await config.update("speed", speed, vscode.ConfigurationTarget.Global);
        client?.configure({ speed });
      }
      break;
    }
    case "TTS Server URL": {
      const value = await vscode.window.showInputBox({
        prompt: "TTS Server URL",
        placeHolder: "e.g. http://localhost:8000",
        value: config.get<string>("ttsServerUrl", "http://localhost:8000"),
      });
      if (value !== undefined) {
        await config.update(
          "ttsServerUrl",
          value,
          vscode.ConfigurationTarget.Global
        );
        // サーバURLの変更はブリッジサーバの再起動が必要
        const action = await vscode.window.showInformationMessage(
          "TTS Reader: Server URL changed. Restart bridge server?",
          "Restart",
          "Later"
        );
        if (action === "Restart") {
          await cmdRestartServer();
        }
      }
      break;
    }
  }
}

async function cmdRestartServer(): Promise<void> {
  log("Restarting bridge server...");
  disconnectClient();
  stopServer();
  await sleep(1000);

  const pythonDir = getPythonDir();
  if (!fs.existsSync(pythonDir)) {
    vscode.window.showErrorMessage(
      "TTS Reader: Python project not found."
    );
    return;
  }

  await startBridgeServer(pythonDir);
  await connectClient();
}

// ---------------------------------------------------------------------------
// 設定同期
// ---------------------------------------------------------------------------

function syncConfiguration(): void {
  if (!client?.isConnected) {
    return;
  }
  const config = vscode.workspace.getConfiguration("ttsReader");
  const params: Record<string, unknown> = {
    caption: config.get<string>("caption", ""),
    seed: config.get<number | null>("seed", null),
  };

  const voice = config.get<string>("voice", "");
  if (voice) {
    params.voice = voice;
  }

  const speed = config.get<number>("speed", 0);
  if (speed > 0) {
    params.speed = speed;
  }

  client.configure(params);
  log("Configuration synced to bridge server.");
}

// ---------------------------------------------------------------------------
// ステータスバー
// ---------------------------------------------------------------------------

type StatusState = "idle" | "reading" | "disconnected" | "error";

function updateStatusBar(state: StatusState): void {
  switch (state) {
    case "idle":
      statusBarItem.text = "$(unmute) TTS";
      statusBarItem.tooltip = "TTS Reader: Ready — Click to read document";
      statusBarItem.command = "ttsReader.speakDocument";
      statusBarItem.backgroundColor = undefined;
      break;
    case "reading":
      statusBarItem.text = "$(sync~spin) TTS Reading...";
      statusBarItem.tooltip = "TTS Reader: Reading — Click to stop";
      statusBarItem.command = "ttsReader.stop";
      statusBarItem.backgroundColor = undefined;
      break;
    case "disconnected":
      statusBarItem.text = "$(mute) TTS (offline)";
      statusBarItem.tooltip = "TTS Reader: Disconnected — Click to reconnect";
      statusBarItem.command = "ttsReader.restartServer";
      statusBarItem.backgroundColor = new vscode.ThemeColor(
        "statusBarItem.warningBackground"
      );
      break;
    case "error":
      statusBarItem.text = "$(error) TTS (error)";
      statusBarItem.tooltip = "TTS Reader: Error — Click to restart";
      statusBarItem.command = "ttsReader.restartServer";
      statusBarItem.backgroundColor = new vscode.ThemeColor(
        "statusBarItem.errorBackground"
      );
      break;
  }
}

// ---------------------------------------------------------------------------
// ユーティリティ
// ---------------------------------------------------------------------------

function log(message: string): void {
  const timestamp = new Date().toISOString().slice(11, 23);
  outputChannel.appendLine(`[${timestamp}] ${message}`);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function execAsync(command: string, cwd?: string): Promise<string> {
  return new Promise((resolve, reject) => {
    log(`Executing: ${command}`);
    cp.exec(
      command,
      { timeout: 120000, cwd, encoding: "utf8" },
      (err, stdout, stderr) => {
        if (stdout) {
          log(`[exec:out] ${stdout.trim()}`);
        }
        if (stderr) {
          log(`[exec:err] ${stderr.trim()}`);
        }
        if (err) {
          reject(new Error(`${err.message}\nstdout: ${stdout}\nstderr: ${stderr}`));
        } else {
          resolve(stdout);
        }
      }
    );
  });
}
