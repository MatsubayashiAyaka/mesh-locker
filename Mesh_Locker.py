# ============================================================================
# Mesh Lock Addon for Blender 3.6+ / 4.2+
# ============================================================================
# 選択した要素（頂点集合）をロックし、移動・削除を防止するアドオン
#
# - 「ロック解除範囲選択」モード中にロック頂点/エッジを色付け表示
#   * ロック頂点: 両端がロックされている頂点
#   * ロックエッジ: 両端の頂点がロックされている辺
#   * 未選択 → 基準色、選択中 → 強調色
# - GPU描画ハンドラを使用（読み取り専用で安全）
# - 表示設定パネル追加（色・サイズ調整可能）
# ============================================================================

bl_info = {
    "name": "Mesh Lock",
    "author": "Claude AI / ChatGPT",
    "version": (1, 2, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Mesh Lock",
    "description": "選択した要素をロックして移動・削除を防止します",
    "category": "Mesh",
}

import bpy
import bmesh
import gpu
from gpu_extras.batch import batch_for_shader
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import IntProperty, PointerProperty, FloatVectorProperty, FloatProperty, BoolProperty

# =============================================================================
# プロパティ
# =============================================================================

class MESHLOCK_Properties(PropertyGroup):
    lock_count: IntProperty(name="ロック頂点数", default=0)
    
    # 表示設定
    show_locked: BoolProperty(
        name="ロック要素を表示",
        description="解除モード中にロック頂点/エッジを色付け表示します",
        default=True
    )
    base_color: FloatVectorProperty(
        name="基準色",
        description="ロック要素（未選択）の色",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(1.0, 0.3, 0.3, 0.9)
    )
    highlight_color: FloatVectorProperty(
        name="強調色",
        description="ロック要素（選択中）の色",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(1.0, 1.0, 0.0, 1.0)
    )
    point_size: FloatProperty(
        name="頂点サイズ",
        description="ロック頂点の表示サイズ",
        min=1.0, max=20.0,
        default=8.0
    )
    line_width: FloatProperty(
        name="エッジ線幅",
        description="ロックエッジの線幅",
        min=1.0, max=10.0,
        default=3.0
    )

# =============================================================================
# 定数
# =============================================================================

LOCK_LAYER_NAME = "mesh_lock_vert"
UNLOCK_MODE_PROP = "_meshlock_unlock_mode"

# GPU描画ハンドラ
_draw_handler = None

# =============================================================================
# ロックデータ管理
# =============================================================================

def ensure_lock_layer(bm: bmesh.types.BMesh):
    layer = bm.verts.layers.int.get(LOCK_LAYER_NAME)
    if layer is None:
        layer = bm.verts.layers.int.new(LOCK_LAYER_NAME)
    return layer

def get_lock_layer(bm: bmesh.types.BMesh):
    return bm.verts.layers.int.get(LOCK_LAYER_NAME)

def _ensure_attr_layer(mesh: bpy.types.Mesh):
    if LOCK_LAYER_NAME not in mesh.attributes:
        mesh.attributes.new(LOCK_LAYER_NAME, 'INT', 'POINT')
    return mesh.attributes.get(LOCK_LAYER_NAME)

def save_lock_to_attributes(obj: bpy.types.Object, bm: bmesh.types.BMesh):
    """BMeshのロックレイヤー値を mesh.attributes(INT/POINT) に保存"""
    mesh = obj.data
    layer = get_lock_layer(bm)
    if layer is None:
        return

    attr = _ensure_attr_layer(mesh)
    if not attr:
        return

    bm.verts.ensure_lookup_table()
    for i, v in enumerate(bm.verts):
        if i < len(attr.data):
            attr.data[i].value = int(v[layer])

def load_lock_from_attributes(obj: bpy.types.Object, bm: bmesh.types.BMesh):
    """mesh.attributes(INT/POINT) からロック状態をBMeshレイヤーへロード"""
    mesh = obj.data
    attr = mesh.attributes.get(LOCK_LAYER_NAME)
    if not attr:
        return

    layer = ensure_lock_layer(bm)

    bm.verts.ensure_lookup_table()
    for i, v in enumerate(bm.verts):
        if i < len(attr.data):
            v[layer] = int(attr.data[i].value)

def count_locked_from_attr(obj: bpy.types.Object) -> int:
    """属性からロック数を数える（安全）"""
    attr = obj.data.attributes.get(LOCK_LAYER_NAME)
    if not attr:
        return 0
    c = 0
    for d in attr.data:
        if int(d.value) == 1:
            c += 1
    return c

def has_any_locked_from_attr(obj: bpy.types.Object) -> bool:
    return count_locked_from_attr(obj) > 0

def count_locked_from_bmesh(bm: bmesh.types.BMesh) -> int:
    layer = get_lock_layer(bm)
    if layer is None:
        return 0
    c = 0
    for v in bm.verts:
        if int(v[layer]) == 1:
            c += 1
    return c

def has_any_locked_from_bmesh(bm: bmesh.types.BMesh) -> bool:
    return count_locked_from_bmesh(bm) > 0

def ensure_lock_attr_synced_from_bmesh(obj: bpy.types.Object, bm: bmesh.types.BMesh) -> bool:
    """
    attributes優先方針における自己修復：
    - BMeshにロックが存在し、attributesが無い/ズレている場合に attributes を同期する
    戻り値: 同期したなら True
    """
    mesh = obj.data
    layer = get_lock_layer(bm)
    if layer is None:
        return False

    bmesh_locked = has_any_locked_from_bmesh(bm)
    attr = mesh.attributes.get(LOCK_LAYER_NAME)

    if not bmesh_locked and not attr:
        return False

    if bmesh_locked and not attr:
        _ensure_attr_layer(mesh)
        save_lock_to_attributes(obj, bm)
        return True

    if attr:
        attr_locked = count_locked_from_attr(obj)
        bm_locked = count_locked_from_bmesh(bm)
        if attr_locked != bm_locked:
            save_lock_to_attributes(obj, bm)
            return True

    return False

# =============================================================================
# 選択/表示ユーティリティ
# =============================================================================

def _clear_selection_history(bm: bmesh.types.BMesh):
    """selection history（active）をクリアして、不整合参照を消す。"""
    try:
        bm.select_history.clear()
    except Exception:
        pass

def apply_hide_to_locked(bm: bmesh.types.BMesh):
    """ロック頂点をhide（選択解除も含む）"""
    layer = get_lock_layer(bm)
    if layer is None:
        return
    for v in bm.verts:
        if int(v[layer]) == 1:
            v.hide = True
            v.select = False

def unhide_locked_only(bm: bmesh.types.BMesh):
    """ロック頂点のみを表示（hide解除）"""
    layer = get_lock_layer(bm)
    if layer is None:
        return 0
    shown = 0
    for v in bm.verts:
        if int(v[layer]) == 1:
            if v.hide:
                shown += 1
            v.hide = False
    return shown

def get_select_mode(context) -> str:
    m = context.tool_settings.mesh_select_mode
    if m[0]:
        return 'VERT'
    if m[1]:
        return 'EDGE'
    if m[2]:
        return 'FACE'
    return 'VERT'

def collect_selected_verts(bm: bmesh.types.BMesh, mode: str):
    """現在の選択（mode）から頂点集合を作る（hideは除外）"""
    verts = set()
    if mode == 'VERT':
        for v in bm.verts:
            if v.select and not v.hide:
                verts.add(v)
    elif mode == 'EDGE':
        for e in bm.edges:
            if e.select and not e.hide:
                for v in e.verts:
                    if not v.hide:
                        verts.add(v)
    elif mode == 'FACE':
        for f in bm.faces:
            if f.select and not f.hide:
                for v in f.verts:
                    if not v.hide:
                        verts.add(v)
    return verts

def selection_has_locked(bm: bmesh.types.BMesh, mode: str) -> bool:
    """選択集合（頂点集合化）がロック頂点に触れているか"""
    layer = get_lock_layer(bm)
    if layer is None:
        return False
    verts = collect_selected_verts(bm, mode)
    if not verts:
        return False
    for v in verts:
        if int(v[layer]) == 1:
            return True
    return False

def selection_locked_verts(bm: bmesh.types.BMesh, mode: str):
    """選択集合（頂点集合化）に含まれるロック頂点だけ返す"""
    layer = get_lock_layer(bm)
    if layer is None:
        return set()
    verts = collect_selected_verts(bm, mode)
    if not verts:
        return set()
    locked = set()
    for v in verts:
        if int(v[layer]) == 1:
            locked.add(v)
    return locked

def is_all_visible_selected(bm: bmesh.types.BMesh, mode: str) -> bool:
    """
    「可視（not hide）の要素が全て選択されているか」を判定
    """
    if mode == 'VERT':
        any_visible = False
        for v in bm.verts:
            if not v.hide:
                any_visible = True
                if not v.select:
                    return False
        return any_visible

    if mode == 'EDGE':
        any_visible = False
        for e in bm.edges:
            if not e.hide:
                any_visible = True
                if not e.select:
                    return False
        return any_visible

    if mode == 'FACE':
        any_visible = False
        for f in bm.faces:
            if not f.hide:
                any_visible = True
                if not f.select:
                    return False
        return any_visible

    return False

def is_all_visible_verts_in_set(bm: bmesh.types.BMesh, verts_set: set) -> bool:
    """頂点削除基準の「全選択」判定"""
    any_visible = False
    for v in bm.verts:
        if not v.hide:
            any_visible = True
            if v not in verts_set:
                return False
    return any_visible

def _deselect_edges_faces_related_to_locked_verts(bm: bmesh.types.BMesh, locked_verts: set):
    """locked_verts に接続する Edge/Face の選択を解除する（クラッシュ回避）"""
    if not locked_verts:
        return
    for v in locked_verts:
        try:
            for e in v.link_edges:
                if e.select:
                    e.select = False
            for f in v.link_faces:
                if f.select:
                    f.select = False
        except ReferenceError:
            continue

# =============================================================================
# 解除モード管理 + 自己修復
# =============================================================================

def is_unlock_mode(obj: bpy.types.Object) -> bool:
    if not obj:
        return False
    try:
        return bool(obj.get(UNLOCK_MODE_PROP, False))
    except Exception:
        return False

def set_unlock_mode(obj: bpy.types.Object, value: bool):
    if not obj:
        return
    try:
        if value:
            obj[UNLOCK_MODE_PROP] = True
        else:
            if UNLOCK_MODE_PROP in obj:
                del obj[UNLOCK_MODE_PROP]
    except Exception:
        pass

def ensure_consistent_lock_state(context, obj: bpy.types.Object):
    """
    ハンドラ無し自己修復：
    - ロック判定の根拠は LOCK_LAYER（attributes優先、BMeshフォールバック）
    - 解除モードは対象オブジェクト限定
    - 明確な矛盾があれば自己修復して復帰
    """
    if not obj or obj.type != 'MESH' or obj.mode != 'EDIT':
        return

    mesh = obj.data
    bm = bmesh.from_edit_mesh(mesh)

    load_lock_from_attributes(obj, bm)
    ensure_lock_attr_synced_from_bmesh(obj, bm)

    if is_unlock_mode(obj):
        if not has_any_locked_from_attr(obj) and not has_any_locked_from_bmesh(bm):
            set_unlock_mode(obj, False)
            _clear_selection_history(bm)
            bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
        return

    layer = get_lock_layer(bm)
    if layer is None:
        return

    need_fix = False
    for v in bm.verts:
        if int(v[layer]) == 1:
            if (not v.hide) or v.select:
                need_fix = True
                break

    if need_fix:
        apply_hide_to_locked(bm)
        _clear_selection_history(bm)
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)

# =============================================================================
# GPU描画（ロック要素の可視化）
# =============================================================================

def draw_locked_elements():
    """解除モード中にロック頂点/エッジを色付け表示"""
    try:
        context = bpy.context
        obj = context.active_object
        
        # 条件チェック
        if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT':
            return
        
        if not is_unlock_mode(obj):
            return
        
        props = context.scene.mesh_lock_props
        if not props.show_locked:
            return
        
        # BMeshを取得（読み取り専用）
        try:
            bm = bmesh.from_edit_mesh(obj.data)
        except Exception:
            return
        
        layer = get_lock_layer(bm)
        if layer is None:
            return
        
        # ワールド行列
        matrix = obj.matrix_world
        
        # ロック頂点を収集（選択/未選択で分類）
        locked_verts_selected = []
        locked_verts_unselected = []
        locked_vert_indices = set()
        
        bm.verts.ensure_lookup_table()
        for v in bm.verts:
            if int(v[layer]) == 1:
                locked_vert_indices.add(v.index)
                world_co = matrix @ v.co
                if v.select:
                    locked_verts_selected.append(world_co)
                else:
                    locked_verts_unselected.append(world_co)
        
        # ロックエッジを収集（両端がロック頂点の辺、選択/未選択で分類）
        locked_edges_selected = []
        locked_edges_unselected = []
        
        bm.edges.ensure_lookup_table()
        for e in bm.edges:
            v0, v1 = e.verts
            if v0.index in locked_vert_indices and v1.index in locked_vert_indices:
                co0 = matrix @ v0.co
                co1 = matrix @ v1.co
                if e.select:
                    locked_edges_selected.append(co0)
                    locked_edges_selected.append(co1)
                else:
                    locked_edges_unselected.append(co0)
                    locked_edges_unselected.append(co1)
        
        # 描画するものがなければ終了
        if not any([locked_verts_selected, locked_verts_unselected, 
                    locked_edges_selected, locked_edges_unselected]):
            return
        
        # シェーダー
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        
        # 色の取得
        base_color = tuple(props.base_color)
        highlight_color = tuple(props.highlight_color)
        
        # GPU状態設定
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.blend_set('ALPHA')
        
        # 未選択エッジを描画（基準色）
        if locked_edges_unselected:
            gpu.state.line_width_set(props.line_width)
            batch = batch_for_shader(shader, 'LINES', {"pos": locked_edges_unselected})
            shader.bind()
            shader.uniform_float("color", base_color)
            batch.draw(shader)
        
        # 選択中エッジを描画（強調色）
        if locked_edges_selected:
            gpu.state.line_width_set(props.line_width + 1.0)
            batch = batch_for_shader(shader, 'LINES', {"pos": locked_edges_selected})
            shader.bind()
            shader.uniform_float("color", highlight_color)
            batch.draw(shader)
        
        # 未選択頂点を描画（基準色）
        if locked_verts_unselected:
            gpu.state.point_size_set(props.point_size)
            batch = batch_for_shader(shader, 'POINTS', {"pos": locked_verts_unselected})
            shader.bind()
            shader.uniform_float("color", base_color)
            batch.draw(shader)
        
        # 選択中頂点を描画（強調色）
        if locked_verts_selected:
            gpu.state.point_size_set(props.point_size + 2.0)
            batch = batch_for_shader(shader, 'POINTS', {"pos": locked_verts_selected})
            shader.bind()
            shader.uniform_float("color", highlight_color)
            batch.draw(shader)
        
        # GPU状態をリセット
        gpu.state.depth_test_set('NONE')
        gpu.state.blend_set('NONE')
        gpu.state.point_size_set(1.0)
        gpu.state.line_width_set(1.0)
        
    except Exception:
        # 描画中の例外は無視（クラッシュ防止）
        pass


def register_draw_handler():
    """GPU描画ハンドラを登録"""
    global _draw_handler
    if _draw_handler is None:
        _draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            draw_locked_elements, (), 'WINDOW', 'POST_VIEW'
        )


def unregister_draw_handler():
    """GPU描画ハンドラを解除"""
    global _draw_handler
    if _draw_handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handler, 'WINDOW')
        _draw_handler = None

# =============================================================================
# ロック / 解除オペレーター
# =============================================================================

class MESHLOCK_OT_lock_selection(Operator):
    bl_idname = "mesh.lock_selection"
    bl_label = "選択をロック"
    bl_description = "選択した要素（頂点集合）をロックしてhideします"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.mode == 'EDIT'

    def execute(self, context):
        obj = context.active_object
        ensure_consistent_lock_state(context, obj)

        bm = bmesh.from_edit_mesh(obj.data)
        layer = ensure_lock_layer(bm)

        mode = get_select_mode(context)
        verts = collect_selected_verts(bm, mode)
        if not verts:
            self.report({'WARNING'}, "選択がありません")
            return {'CANCELLED'}

        if is_unlock_mode(obj):
            set_unlock_mode(obj, False)

        if mode == 'EDGE':
            for e in bm.edges:
                if e.select:
                    e.select = False
        elif mode == 'FACE':
            for f in bm.faces:
                if f.select:
                    f.select = False

        newly_locked = 0
        locked_verts = set()

        for v in verts:
            if int(v[layer]) != 1:
                v[layer] = 1
                newly_locked += 1
            v.hide = True
            v.select = False
            locked_verts.add(v)

        _deselect_edges_faces_related_to_locked_verts(bm, locked_verts)
        _clear_selection_history(bm)

        save_lock_to_attributes(obj, bm)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

        context.scene.mesh_lock_props.lock_count = count_locked_from_attr(obj)

        self.report({'INFO'}, f"{newly_locked}個の頂点をロックしました")
        return {'FINISHED'}


class MESHLOCK_OT_begin_unlock_select(Operator):
    bl_idname = "mesh.lock_begin_unlock_select"
    bl_label = "ロック解除範囲選択"
    bl_description = "ロック頂点のみを表示して、解除したいロック頂点を選択できる状態にします"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.mode == 'EDIT'

    def execute(self, context):
        obj = context.active_object
        ensure_consistent_lock_state(context, obj)

        bm = bmesh.from_edit_mesh(obj.data)
        load_lock_from_attributes(obj, bm)

        locked_count_attr = count_locked_from_attr(obj)
        locked_count_bm = count_locked_from_bmesh(bm)

        if locked_count_attr == 0 and locked_count_bm == 0:
            set_unlock_mode(obj, False)
            self.report({'WARNING'}, "ロックされた要素がありません")
            return {'CANCELLED'}

        ensure_lock_attr_synced_from_bmesh(obj, bm)

        shown = unhide_locked_only(bm)

        for e in bm.edges:
            if e.select:
                e.select = False
        for f in bm.faces:
            if f.select:
                f.select = False

        _clear_selection_history(bm)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

        set_unlock_mode(obj, True)

        context.scene.mesh_lock_props.lock_count = count_locked_from_attr(obj)

        if shown == 0:
            self.report({'INFO'}, "ロック頂点を選択して「選択箇所のロック解除」を実行してください")
        else:
            self.report({'INFO'}, f"{shown}個のロック頂点を表示しました。解除したい範囲を選択してください")
        return {'FINISHED'}


class MESHLOCK_OT_cancel_unlock_mode(Operator):
    bl_idname = "mesh.lock_cancel_unlock_mode"
    bl_label = "ロック解除キャンセル"
    bl_description = "ロック解除モードをキャンセルして、ロック頂点を再度非表示にします"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.mode == 'EDIT' and is_unlock_mode(obj)

    def execute(self, context):
        obj = context.active_object
        
        bm = bmesh.from_edit_mesh(obj.data)
        load_lock_from_attributes(obj, bm)
        
        # ロック頂点を再度hideにする
        apply_hide_to_locked(bm)
        _clear_selection_history(bm)
        
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        
        # 解除モードをオフにする
        set_unlock_mode(obj, False)
        
        self.report({'INFO'}, "ロック解除モードをキャンセルしました")
        return {'FINISHED'}


class MESHLOCK_OT_unlock_selection(Operator):
    bl_idname = "mesh.unlock_selection"
    bl_label = "選択箇所のロック解除"
    bl_description = "（解除モード中のみ）選択に含まれるロック頂点を解除し、残りロック頂点を再hideします"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.mode == 'EDIT'

    def execute(self, context):
        obj = context.active_object
        ensure_consistent_lock_state(context, obj)

        if not is_unlock_mode(obj):
            self.report({'WARNING'}, "先に「ロック解除範囲選択」を実行してください")
            return {'CANCELLED'}

        bm = bmesh.from_edit_mesh(obj.data)
        load_lock_from_attributes(obj, bm)
        layer = get_lock_layer(bm)
        if layer is None:
            set_unlock_mode(obj, False)
            self.report({'WARNING'}, "ロック情報が見つかりません")
            return {'CANCELLED'}

        mode = get_select_mode(context)

        locked_to_unlock = selection_locked_verts(bm, mode)

        if not locked_to_unlock:
            self.report({'WARNING'}, "選択にロック頂点が含まれていません。解除したいロック頂点を選択してください")
            return {'CANCELLED'}

        for v in locked_to_unlock:
            v[layer] = 0

        apply_hide_to_locked(bm)
        _clear_selection_history(bm)

        save_lock_to_attributes(obj, bm)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

        set_unlock_mode(obj, False)

        context.scene.mesh_lock_props.lock_count = count_locked_from_attr(obj)

        self.report({'INFO'}, f"{len(locked_to_unlock)}個の頂点のロックを解除しました")
        return {'FINISHED'}


class MESHLOCK_OT_unlock_all(Operator):
    bl_idname = "mesh.unlock_all"
    bl_label = "すべて解除"
    bl_description = "すべての頂点のロックを解除します"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.mode == 'EDIT'

    def execute(self, context):
        obj = context.active_object
        ensure_consistent_lock_state(context, obj)

        bm = bmesh.from_edit_mesh(obj.data)
        load_lock_from_attributes(obj, bm)
        layer = get_lock_layer(bm)

        if layer is None:
            set_unlock_mode(obj, False)
            self.report({'WARNING'}, "ロックされた要素がありません")
            return {'CANCELLED'}

        changed = 0
        for v in bm.verts:
            if int(v[layer]) == 1:
                changed += 1
            v[layer] = 0
            v.hide = False

        if changed == 0:
            set_unlock_mode(obj, False)
            self.report({'WARNING'}, "ロックされた頂点がありません")
            return {'CANCELLED'}

        _clear_selection_history(bm)

        save_lock_to_attributes(obj, bm)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

        set_unlock_mode(obj, False)

        context.scene.mesh_lock_props.lock_count = 0

        self.report({'INFO'}, f"{changed}個の頂点のロックを解除しました")
        return {'FINISHED'}

# =============================================================================
# ガード：削除（Xキー用 - ダイアログなし）
# =============================================================================

class MESHLOCK_OT_guard_delete_x(Operator):
    bl_idname = "mesh.lock_guard_delete_x"
    bl_label = "Mesh Lock: Guard Delete (X)"
    bl_description = "ロック頂点に触れている削除、およびロック存在時の全選択削除をブロックします（ダイアログなし）"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.mode == 'EDIT'

    def execute(self, context):
        obj = context.active_object
        ensure_consistent_lock_state(context, obj)

        locked_exists = has_any_locked_from_attr(obj)
        bm = bmesh.from_edit_mesh(obj.data)
        mode = get_select_mode(context)

        selected_verts = collect_selected_verts(bm, mode)
        if not selected_verts:
            self.report({'WARNING'}, "削除できる選択がありません")
            return {'CANCELLED'}

        if selection_has_locked(bm, mode):
            self.report({'WARNING'}, "ロックされた要素が選択に含まれているため削除できません")
            return {'CANCELLED'}

        if locked_exists and is_all_visible_verts_in_set(bm, selected_verts):
            self.report({'WARNING'}, "ロックされた要素が存在するため、全選択での削除はできません")
            return {'CANCELLED'}

        # 選択を再構成：ロックされていない頂点のみを選択状態にする
        for e in bm.edges:
            if e.select:
                e.select = False
        for f in bm.faces:
            if f.select:
                f.select = False
        for v in bm.verts:
            if v.select:
                v.select = False
        for v in selected_verts:
            if not v.hide:
                v.select = True

        _clear_selection_history(bm)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

        # ダイアログなしで直接削除（頂点削除でロック頂点を確実に保護）
        bpy.ops.mesh.delete(type='VERT')
        return {'FINISHED'}

# =============================================================================
# ガード：削除（DEL/BACK_SPACEキー用 - ダイアログあり）
# =============================================================================

class MESHLOCK_OT_guard_delete_dialog(Operator):
    bl_idname = "mesh.lock_guard_delete_dialog"
    bl_label = "Mesh Lock: Guard Delete (Dialog)"
    bl_description = "ロック頂点に触れている削除、およびロック存在時の全選択削除をブロックします（ダイアログ表示）"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.mode == 'EDIT'

    def execute(self, context):
        obj = context.active_object
        ensure_consistent_lock_state(context, obj)

        locked_exists = has_any_locked_from_attr(obj)
        bm = bmesh.from_edit_mesh(obj.data)
        mode = get_select_mode(context)

        selected_verts = collect_selected_verts(bm, mode)
        if not selected_verts:
            self.report({'WARNING'}, "削除できる選択がありません")
            return {'CANCELLED'}

        if selection_has_locked(bm, mode):
            self.report({'WARNING'}, "ロックされた要素が選択に含まれているため削除できません")
            return {'CANCELLED'}

        if locked_exists and is_all_visible_verts_in_set(bm, selected_verts):
            self.report({'WARNING'}, "ロックされた要素が存在するため、全選択での削除はできません")
            return {'CANCELLED'}

        # 選択を再構成：ロック頂点に接続していない要素のみを選択状態にする
        
        # フェースの選択を再構成：すべての頂点がselected_vertsに含まれるフェースのみ選択維持
        for f in bm.faces:
            if f.select:
                all_verts_safe = all(v in selected_verts for v in f.verts)
                if not all_verts_safe:
                    f.select = False
        
        # エッジの選択を再構成：両端の頂点が両方ともselected_vertsに含まれるエッジのみ選択維持
        for e in bm.edges:
            if e.select:
                both_verts_safe = all(v in selected_verts for v in e.verts)
                if not both_verts_safe:
                    e.select = False
        
        # 頂点の選択を再構成
        for v in bm.verts:
            if v.select:
                v.select = False
        for v in selected_verts:
            if not v.hide:
                v.select = True

        _clear_selection_history(bm)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)

        # 完全なDeleteメニューを表示（Dissolve系、Collapse、Edge Loopsなどを含む）
        bpy.ops.wm.call_menu(name='VIEW3D_MT_edit_mesh_delete')
        return {'FINISHED'}

# =============================================================================
# ガード：移動
# =============================================================================

class MESHLOCK_OT_guard_translate(Operator):
    bl_idname = "mesh.lock_guard_translate"
    bl_label = "Mesh Lock: Guard Translate"
    bl_description = "ロック頂点に触れている移動、およびロック存在時の全選択移動をブロックします"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.mode == 'EDIT'

    def execute(self, context):
        obj = context.active_object
        ensure_consistent_lock_state(context, obj)

        locked_exists = has_any_locked_from_attr(obj)
        bm = bmesh.from_edit_mesh(obj.data)
        mode = get_select_mode(context)

        selected_verts = collect_selected_verts(bm, mode)
        if not selected_verts:
            self.report({'WARNING'}, "移動できる選択がありません")
            return {'CANCELLED'}

        if selection_has_locked(bm, mode):
            self.report({'WARNING'}, "ロックされた要素が選択に含まれているため移動できません")
            return {'CANCELLED'}

        if locked_exists and is_all_visible_selected(bm, mode):
            self.report({'WARNING'}, "ロックされた要素が存在するため、全選択での移動はできません")
            return {'CANCELLED'}

        bpy.ops.transform.translate('INVOKE_DEFAULT')
        return {'FINISHED'}

# =============================================================================
# UI
# =============================================================================

class MESHLOCK_PT_panel(Panel):
    bl_label = "Mesh Lock"
    bl_idname = "MESHLOCK_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Mesh Lock'

    def draw(self, context):
        layout = self.layout
        obj = context.active_object

        if obj and obj.type == 'MESH' and obj.mode == 'EDIT':
            try:
                ensure_consistent_lock_state(context, obj)
            except Exception:
                pass

        col = layout.column(align=True)
        col.scale_y = 1.2

        col.operator("mesh.lock_selection", icon='LOCKED')
        
        # 解除モード中は「ロック解除キャンセル」ボタンを表示、そうでなければ「ロック解除範囲選択」ボタン
        if obj and obj.type == 'MESH' and obj.mode == 'EDIT' and is_unlock_mode(obj):
            col.operator("mesh.lock_cancel_unlock_mode", text="ロック解除キャンセル", icon='CANCEL')
        else:
            col.operator("mesh.lock_begin_unlock_select", text="ロック解除範囲選択", icon='RESTRICT_SELECT_OFF')

        row = col.row(align=True)
        row.enabled = False
        row.operator("mesh.unlock_selection", text="選択箇所のロック解除", icon='UNLOCKED')

        if obj and obj.type == 'MESH' and obj.mode == 'EDIT' and is_unlock_mode(obj):
            try:
                bm = bmesh.from_edit_mesh(obj.data)
                load_lock_from_attributes(obj, bm)
                mode = get_select_mode(context)
                if selection_has_locked(bm, mode):
                    row.enabled = True
            except Exception:
                row.enabled = False

        col.operator("mesh.unlock_all", icon='X')

        layout.separator()

        lock_count = 0
        if obj and obj.type == 'MESH':
            try:
                lock_count = count_locked_from_attr(obj)
            except Exception:
                lock_count = 0
        layout.label(text=f"ロック頂点: {lock_count}")

        # 解除モード表示
        if obj and obj.type == 'MESH' and is_unlock_mode(obj):
            box = layout.box()
            box.label(text="解除モード中", icon='INFO')

        if not (obj and obj.type == 'MESH' and obj.mode == 'EDIT'):
            layout.separator()
            layout.label(text="Editモードで使用", icon='INFO')


class MESHLOCK_PT_display_panel(Panel):
    bl_label = "表示設定"
    bl_idname = "MESHLOCK_PT_display_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Mesh Lock'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.mesh_lock_props

        layout.prop(props, "show_locked")
        
        col = layout.column()
        col.enabled = props.show_locked
        col.prop(props, "base_color", text="基準色")
        col.prop(props, "highlight_color", text="強調色")
        col.prop(props, "point_size")
        col.prop(props, "line_width")

# =============================================================================
# キーマップ
# =============================================================================

addon_keymaps = []

def _km_key(km: bpy.types.KeyMap):
    return (km.name, km.space_type, km.region_type)

def _copy_modifiers_from(src_kmi: bpy.types.KeyMapItem, dst_kmi: bpy.types.KeyMapItem):
    dst_kmi.ctrl = src_kmi.ctrl
    dst_kmi.shift = src_kmi.shift
    dst_kmi.alt = src_kmi.alt
    dst_kmi.oskey = src_kmi.oskey
    dst_kmi.any = src_kmi.any
    try:
        dst_kmi.key_modifier = src_kmi.key_modifier
    except Exception:
        pass

def register_keymaps():
    wm = bpy.context.window_manager
    kc_def = wm.keyconfigs.default if wm and wm.keyconfigs else None
    kc_add = wm.keyconfigs.addon if wm and wm.keyconfigs else None
    if not kc_def or not kc_add:
        return

    created = {}

    def get_or_make_addon_km(src_km: bpy.types.KeyMap):
        key = _km_key(src_km)
        if key in created:
            return created[key]
        try:
            km = kc_add.keymaps.new(
                name=src_km.name,
                space_type=src_km.space_type,
                region_type=src_km.region_type,
            )
        except TypeError:
            km = kc_add.keymaps.new(name=src_km.name, space_type=src_km.space_type)
        created[key] = km
        return km

    for km in kc_def.keymaps:
        if km.is_modal:
            continue

        for kmi in km.keymap_items:
            # mesh.delete のキーマップをオーバーライド
            if kmi.idname == "mesh.delete" and kmi.value == "PRESS":
                addon_km = get_or_make_addon_km(km)
                # Xキー → ダイアログなし
                if kmi.type == "X":
                    new_kmi = addon_km.keymap_items.new("mesh.lock_guard_delete_x", kmi.type, "PRESS")
                    _copy_modifiers_from(kmi, new_kmi)
                    addon_keymaps.append((addon_km, new_kmi))
                # DEL/BACK_SPACE → ダイアログあり
                elif kmi.type in ("DEL", "BACK_SPACE"):
                    new_kmi = addon_km.keymap_items.new("mesh.lock_guard_delete_dialog", kmi.type, "PRESS")
                    _copy_modifiers_from(kmi, new_kmi)
                    addon_keymaps.append((addon_km, new_kmi))

            if kmi.idname == "transform.translate" and kmi.type == "G" and kmi.value == "PRESS":
                addon_km = get_or_make_addon_km(km)
                new_kmi = addon_km.keymap_items.new("mesh.lock_guard_translate", "G", "PRESS")
                _copy_modifiers_from(kmi, new_kmi)
                addon_keymaps.append((addon_km, new_kmi))

    try:
        km = kc_add.keymaps.new(name="Mesh", space_type='EMPTY')
        # Xキー → ダイアログなし
        kmi = km.keymap_items.new("mesh.lock_guard_delete_x", 'X', 'PRESS')
        addon_keymaps.append((km, kmi))
        # DEL/BACK_SPACE → ダイアログあり
        for key in ('DEL', 'BACK_SPACE'):
            kmi = km.keymap_items.new("mesh.lock_guard_delete_dialog", key, 'PRESS')
            addon_keymaps.append((km, kmi))
        
        kmi = km.keymap_items.new("mesh.lock_guard_translate", 'G', 'PRESS')
        addon_keymaps.append((km, kmi))

        kmi = km.keymap_items.new("mesh.lock_selection", 'L', 'PRESS', ctrl=True, shift=True)
        addon_keymaps.append((km, kmi))
        kmi = km.keymap_items.new("mesh.lock_begin_unlock_select", 'U', 'PRESS', ctrl=True, shift=True)
        addon_keymaps.append((km, kmi))
    except Exception:
        pass

def unregister_keymaps():
    for km, kmi in addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    addon_keymaps.clear()

# =============================================================================
# 登録
# =============================================================================

classes = (
    MESHLOCK_Properties,
    MESHLOCK_OT_lock_selection,
    MESHLOCK_OT_begin_unlock_select,
    MESHLOCK_OT_cancel_unlock_mode,
    MESHLOCK_OT_unlock_selection,
    MESHLOCK_OT_unlock_all,
    MESHLOCK_OT_guard_delete_x,
    MESHLOCK_OT_guard_delete_dialog,
    MESHLOCK_OT_guard_translate,
    MESHLOCK_PT_panel,
    MESHLOCK_PT_display_panel,
)

def register():
    for c in classes:
        bpy.utils.register_class(c)

    bpy.types.Scene.mesh_lock_props = PointerProperty(type=MESHLOCK_Properties)

    try:
        ctx = bpy.context
        obj = ctx.active_object
        if obj and obj.type == 'MESH':
            ctx.scene.mesh_lock_props.lock_count = count_locked_from_attr(obj)
            if obj.mode == 'EDIT':
                bm = bmesh.from_edit_mesh(obj.data)
                load_lock_from_attributes(obj, bm)
                ensure_lock_attr_synced_from_bmesh(obj, bm)
                apply_hide_to_locked(bm)
                _clear_selection_history(bm)
                bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
                set_unlock_mode(obj, False)
    except Exception:
        pass

    register_keymaps()
    register_draw_handler()
    print("Mesh Lock v1.2.0: enabled")

def unregister():
    unregister_draw_handler()
    unregister_keymaps()

    if hasattr(bpy.types.Scene, "mesh_lock_props"):
        del bpy.types.Scene.mesh_lock_props

    try:
        for obj in bpy.data.objects:
            if UNLOCK_MODE_PROP in obj:
                del obj[UNLOCK_MODE_PROP]
    except Exception:
        pass

    for c in reversed(classes):
        bpy.utils.unregister_class(c)

    print("Mesh Lock v1.2.0: disabled")

if __name__ == "__main__":
    register()
