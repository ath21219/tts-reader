import WebSocket from "ws";
import {
  BridgeRequest,
  BridgeEvent,
  PlaybackEventData,
  PlaybackCallback,
  DoneCallback,
  ErrorCallback,
} from "./types";

/**
 * Bridge Server との WebSocket 通信を管理するクライアント
 */
export class BridgeClient {
  private ws: WebSocket | null = null;
  private url: string;
  private reconnectInterval = 3000;
  private maxReconnectAttempts = 5;
  private reconnectAttempts = 0;
  private disposed = false;

  // コールバック
  private onPlayback: PlaybackCallback | null = null;
  private onDone: DoneCallback | null = null;
  private onError: ErrorCallback | null = null;
  private onConnected: (() => void) | null = null;
  private onDisconnected: (() => void) | null = null;

  constructor(host: string, port: number) {
    this.url = `ws://${host}:${port}`;
  }

  // ----- lifecycle -------------------------------------------------------

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      if (this.disposed) {
        reject(new Error("Client is disposed"));
        return;
      }

      this.ws = new WebSocket(this.url);

      this.ws.on("open", () => {
        this.reconnectAttempts = 0;
        this.onConnected?.();
        resolve();
      });

      this.ws.on("message", (raw: WebSocket.RawData) => {
        this.handleMessage(raw.toString());
      });

      this.ws.on("close", () => {
        this.onDisconnected?.();
        if (!this.disposed) {
          this.tryReconnect();
        }
      });

      this.ws.on("error", (err: Error) => {
        if (this.reconnectAttempts === 0) {
          reject(err);
        }
      });
    });
  }

  disconnect(): void {
    this.disposed = true;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  // ----- commands --------------------------------------------------------

  speak(text: string): void {
    this.send({ method: "speak", params: { text } });
  }

  stop(): void {
    this.send({ method: "stop" });
  }

  configure(params: Record<string, unknown>): void {
    this.send({ method: "configure", params });
  }

  // ----- callbacks -------------------------------------------------------

  setOnPlayback(cb: PlaybackCallback): void {
    this.onPlayback = cb;
  }
  setOnDone(cb: DoneCallback): void {
    this.onDone = cb;
  }
  setOnError(cb: ErrorCallback): void {
    this.onError = cb;
  }
  setOnConnected(cb: () => void): void {
    this.onConnected = cb;
  }
  setOnDisconnected(cb: () => void): void {
    this.onDisconnected = cb;
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  // ----- internal --------------------------------------------------------

  private send(msg: BridgeRequest): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  private handleMessage(raw: string): void {
    let msg: BridgeEvent;
    try {
      msg = JSON.parse(raw) as BridgeEvent;
    } catch {
      return;
    }

    switch (msg.event) {
      case "playback":
        if (msg.data && "state" in msg.data && "chunk" in msg.data) {
          this.onPlayback?.(msg.data as PlaybackEventData);
        }
        break;
      case "done":
        this.onDone?.();
        break;
      case "error":
        if (msg.data && "message" in msg.data) {
          this.onError?.((msg.data as { message: string }).message);
        }
        break;
    }
  }

  private tryReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      return;
    }
    this.reconnectAttempts++;
    setTimeout(() => {
      if (!this.disposed) {
        this.connect().catch(() => { });
      }
    }, this.reconnectInterval);
  }
}
