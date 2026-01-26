# CHANGELOG

このプロジェクトの変更履歴を記録します。  
形式は [Keep a Changelog](https://keepachangelog.com/) を参考にしています。

---

## [1.1.1]

### Fixed
- Delete guard continued intercepting normal Delete operations after all locks were removed
- Now correctly returns PASS_THROUGH when no locked elements exist

### Changed
- Delete behavior fully restored to Blender default when lock count is 0
- More predictable and stable guard logic

### Notes
- This version fixes unintended mesh deletion affecting non-selected elements after unlock
- Recommended update for all users


## [1.0.1]

### Added
- 「ロック解除範囲選択」モード中にロック頂点/ロックエッジを色付け表示
  - ロック頂点: 未選択は基準色、選択中は強調色
  - ロックエッジ: 両端がロック頂点の辺のみを表示
- 表示設定パネル追加（色・サイズ調整）
- GPU描画ハンドラによる読み取り専用の重ね描画（メッシュデータ非破壊）

### Technical
- `SpaceView3D.draw_handler_add` による描画
- 深度テスト（LESS_EQUAL）による奥行き処理

---

## [1.0.0]

### Added
- 初期リリース
- 選択した頂点/辺/面をロックして編集不可に
- 移動（G）・削除（X / DEL / Backspace）からの保護
- ロック情報の永続化（`mesh.attributes` に保存、`.blend` ファイルに含まれる）
- 2段階ロック解除（部分解除対応）
  - 「ロック解除範囲選択」→ ロック頂点を表示
  - 「選択箇所のロック解除」→ 選択した部分のみ解除
- Blender 4.2 対応（Panel.draw() の例外問題を解決済み）
- 自己修復機能（BMesh と attributes の整合性自動修正）
