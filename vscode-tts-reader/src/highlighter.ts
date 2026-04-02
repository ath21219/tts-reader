import * as vscode from "vscode";
import { PlaybackEventData } from "./types";

/**
 * エディタ上で読み上げ中のテキスト範囲をハイライトする
 */
export class Highlighter {
  private decoration: vscode.TextEditorDecorationType;
  private activeRanges: vscode.Range[] = [];

  constructor() {
    this.decoration = vscode.window.createTextEditorDecorationType({
      backgroundColor: new vscode.ThemeColor(
        "editor.findMatchHighlightBackground"
      ),
      borderRadius: "2px",
      isWholeLine: false,
    });
  }

  /**
   * 再生イベントに基づいてハイライトを更新する
   */
  update(editor: vscode.TextEditor, data: PlaybackEventData): void {
    if (data.state === "playing" && data.chunk.type === "text") {
      const startPos = editor.document.positionAt(data.chunk.source_offset);
      const endPos = editor.document.positionAt(
        data.chunk.source_offset + data.chunk.source_length
      );
      const range = new vscode.Range(startPos, endPos);
      this.activeRanges = [range];
      editor.setDecorations(this.decoration, this.activeRanges);

      // ハイライト位置が見えるようスクロール
      editor.revealRange(range, vscode.TextEditorRevealType.InCenterIfOutsideViewport);
    } else if (data.state === "idle") {
      // このチャンクの再生完了 — ハイライトを維持（次のチャンクで上書き）
    }
  }

  /**
   * ハイライトをすべてクリアする
   */
  clear(editor: vscode.TextEditor): void {
    this.activeRanges = [];
    editor.setDecorations(this.decoration, []);
  }

  dispose(): void {
    this.decoration.dispose();
  }
}
