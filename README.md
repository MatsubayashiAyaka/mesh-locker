# Mesh Locker（Blender Add-on）

BlenderのEditモードで、選択したメッシュ要素（頂点集合）を「ロック」して、誤って移動・削除してしまう事故を防ぐアドオンです。  
ロック情報は `mesh.attributes`（INT / POINT）に保存され`.blend` に永続化されます。

---

## 対応環境

- Blender: 3.6 以降（4.2 以降推奨）
- OS: Windows / macOS / Linux

---

## インストール

### 1) Releases から入れる（推奨）

1. GitHub の [Releases](https://github.com/MatsubayashiAyaka/mesh-locker/releases) から最新の `Mesh_Locker.py` をダウンロード
2. Blender → `Edit` → `Preferences` → `Add-ons`
3. 右上の `Install...` → `Mesh_Locker.py` を選択
4. 一覧で Add-on を有効化（チェックON）

### 2) 手動で配置する

1. リポジトリをクローン、または ZIP をダウンロード
2. `Mesh_Locker.py` を addons フォルダへコピー
   - Windows: `%APPDATA%\Blender Foundation\Blender\4.2\scripts\addons\`
   - macOS: `~/Library/Application Support/Blender/4.2/scripts/addons/`
   - Linux: `~/.config/blender/4.2/scripts/addons/`
3. Blender を再起動して Add-on を有効化

---

## 使い方（基本フロー）

### パネルの場所

- 3D Viewport → サイドバー（Nキー）→ `Mesh Lock` タブ

### ロックする

1. Editモードで、ロックしたい頂点/辺/面を選択  
   ※内部的には「頂点集合」としてロックします
2. `選択をロック` を押下

### ロックを解除する（部分解除）

1. `ロック解除範囲選択` を押下  
   - ロック頂点のみが表示され、解除対象を選択しやすい状態になります
   - このモード中、ロック頂点/ロックエッジが色で強調表示されます（v1.1.0以降）
     - 未選択: 基準色
     - 選択中: 強調色
     - ロックエッジは「両端がロック頂点」の辺のみ
2. 解除したい箇所を選択
3. `選択箇所のロック解除` を押下

### ロックを解除する（全解除）

- `すべて解除` を押下

---

## 編集の保護（ガード）

ロック頂点が存在する場合、以下をブロックします。

- **移動（G）**
  - ロック要素に触れている移動をブロック
  - ロック存在時の全選択移動をブロック（クラッシュ回避）

- **削除（X / DEL / Backspace）**
  - ロック要素に触れている削除をブロック
  - ロック存在時の全選択削除をブロック

---

## データ仕様（概要）

- ロック情報: `mesh.attributes["mesh_lock_vert"]`（INT / POINT）
- 解除モード状態: Object カスタムプロパティ `_meshlock_unlock_mode`
- 解除モード中のハイライト: GPU描画（読み取り専用の重ね描画）
  - メッシュデータ（マテリアル/頂点カラー等）は変更しません

---

## バージョン履歴

変更履歴は [CHANGELOG.md](CHANGELOG.md) を参照してください。

---

## ライセンス

MIT License
