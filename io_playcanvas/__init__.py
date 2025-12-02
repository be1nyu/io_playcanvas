import os
import json
import math
import bpy
import bmesh
from mathutils import Matrix, Vector, Euler
from bpy.types import Operator
from bpy.props import StringProperty, CollectionProperty
from bpy_extras.io_utils import ImportHelper

bl_info = {
    "name": "PlayCanvas Importer",
    "author": "be1nyu",
    "version": (0, 0, 1),
    "blender": (4, 0, 0),
    "location": "File > Import > PlayCanvas (.json)",
    "description": "Import PlayCanvas models into active collection",
    "category": "Import-Export",
    "support": "COMMUNITY",
    'tracker_url': 'https://github.com/be1nyu/io_playcanvas/issues'
}

# degree > radian 변환 / euler > 회전 행렬
def deg_to_rad_euler(e):
    if not e:
        return Euler((0.0, 0.0, 0.0), 'XYZ')
    return Euler((math.radians(v) for v in e), 'XYZ')

# 변환 행렬 생성
def make_local_matrix(pos=None, rot=None, scale=None):
    pos = Vector(pos or (0, 0, 0))
    rot = deg_to_rad_euler(rot or (0, 0, 0)).to_matrix().to_4x4()
    scale = Matrix.Diagonal(Vector(scale or (1, 1, 1)).to_4d())
    return Matrix.Translation(pos) @ rot @ scale

# 월드 행렬 계산
def compute_world_matrices(nodes):
    world_mats = {}
    def calc(idx):
        if idx in world_mats:
            return world_mats[idx]
        node = nodes[idx]
        local = make_local_matrix(node.get("position"), node.get("rotation"), node.get("scale"))
        parent_idx = node.get("parent", -1)
        if parent_idx >= 0:
            world_mats[idx] = calc(parent_idx) @ local
        else:
            world_mats[idx] = local
        return world_mats[idx]
    for i in range(len(nodes)):
        calc(i)
    return world_mats

# uv 레이어 추가
def assign_uv_layer(mesh, uv_data, name="UVMap", flip_v=True):
    if not uv_data or len(uv_data) // 2 != len(mesh.vertices):
        return
    uv_layer = mesh.uv_layers.new(name=name).data
    for poly in mesh.polygons:
        for loop_idx in poly.loop_indices:
            v_idx = mesh.loops[loop_idx].vertex_index
            u = uv_data[v_idx * 2]
            v = 1.0 - uv_data[v_idx * 2 + 1] if flip_v else uv_data[v_idx * 2 + 1]
            uv_layer[loop_idx].uv = (u, v)

# 오브젝트 생성
def build_mesh_object(name, positions, normals=None, indices=None, uv0=None, uv1=None):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    
    # 점 생성
    for i in range(0, len(positions), 3):
        bm.verts.new((positions[i], positions[i+1], positions[i+2]))
    bm.verts.ensure_lookup_table()
    
    # 면 생성
    if indices and len(indices) >= 3:
        for i in range(0, len(indices), 3):
            try:
                bm.faces.new((bm.verts[indices[i]], bm.verts[indices[i+1]], bm.verts[indices[i+2]]))
            except:
                pass
    else:
        for i in range(0, len(bm.verts), 3):
            if i + 2 >= len(bm.verts):
                break
            try:
                bm.faces.new((bm.verts[i], bm.verts[i+1], bm.verts[i+2]))
            except:
                pass
    
    bm.to_mesh(mesh)
    bm.free()
    
    # 노멀 적용
    if normals and len(normals) == len(positions):
        try:
            custom_normals = [(normals[i], normals[i+1], normals[i+2]) for i in range(0, len(normals), 3)]
            mesh.normals_split_custom_set_from_vertices(custom_normals)
        except:
            mesh.calc_normals()
    else:
        mesh.calc_normals()
    
    mesh.validate()
    mesh.update()
    
    # UV 적용
    assign_uv_layer(mesh, uv0, "UVMap")
    assign_uv_layer(mesh, uv1, "UV2")
    
    return bpy.data.objects.new(name, mesh)

# 텍스처 로드 = 좀 불안함
def load_texture(mat, path):
    if not path or not os.path.exists(path):
        return
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat_tree.links
    bsdf = nodes.get("Principled BSDF") or nodes.new("ShaderNodeBsdfPrincipled")
    tex = nodes.new("ShaderNodeTexImage")
    try:
        img = bpy.data.images.load(path, check_existing=True)
        tex.image = img
        links.new(bsdf.inputs["Base Color"], tex.outputs["Color"])
    except:
        pass
    

# json 구조 파싱
def parse_playcanvas_data(data):
    model = data.get("model", {}) or data
    parsed = {"nodes": [], "meshes": [], "instances": [], "materials": []}
    
    # 노드 정보 정리
    nodes_raw = model.get("nodes") or data.get("nodes") or []
    parents_raw = model.get("parents") or data.get("parents") or []
    for i, n in enumerate(nodes_raw):
        n = n if isinstance(n, dict) else {}
        parsed["nodes"].append({
            "index": i,
            "name": n.get("name", f"Node_{i}"),
            "position": n.get("position"),
            "rotation": n.get("rotation"),
            "scale": n.get("scale"),
            "parent": parents_raw[i] if i < len(parents_raw) else -1,
        })
        
    # 위치/노멀/uv 추출
    vertex_buffers = model.get("vertices") or []
    for mi, m in enumerate(model.get("meshes") or []):
        m = m if isinstance(m, dict) else {}
        vb_idx = m.get("vertices")
        pos = nrm = uv0 = uv1 = None
        if isinstance(vb_idx, int) and 0 <= vb_idx < len(vertex_buffers):
            vb = vertex_buffers[vb_idx] or {}
            pos = vb.get("position", {}).get("data")
            nrm = vb.get("normal", {}).get("data")
            uv0 = vb.get("texCoord0", {}).get("data")
            uv1 = vb.get("texCoord1", {}).get("data")

        parsed["meshes"].append({
            "name": m.get("name", f"Mesh_{mi}"),
            "positions": pos,
            "normals": nrm,
            "indices": m.get("indices") or m.get("triangles"),
            "uv0": uv0,
            "uv1": uv1,
        })

    for inst in model.get("meshInstances", []):
        if isinstance(inst, dict):
            parsed["instances"].append({
                "node": inst.get("node"),
                "mesh": inst.get("mesh"),
                "material": inst.get("material"),
            })

    for i, m in enumerate(model.get("materials") or data.get("materials") or []):
        m = m if isinstance(m, dict) else {}
        diffuse_map = m.get("diffuseMap")
        diffuse_color = m.get("diffuse", {}).get("data") if isinstance(m.get("diffuse"), dict) else None
        parsed["materials"].append({
            "index": i,
            "name": m.get("name", f"Material_{i}"),
            "diffuse_map": diffuse_map,
            "diffuse_color": diffuse_color,
        })

    return parsed

# 메인
class ImportPlayCanvas(Operator, ImportHelper):
    bl_idname = "import_scene.playcanvas"
    bl_label = "Import PlayCanvas"
    bl_options = {'PRESET', 'UNDO'}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN'})

    def execute(self, context):
        if not self.files:
            self.report({'WARNING'}, "Select a file")
            return {'CANCELLED'}

        directory = os.path.dirname(self.filepath)
        conv_mat = Matrix.Rotation(-math.pi / 2, 4, 'X')
        target_collection = context.collection or context.scene.collection

        for file_entry in self.files:
            filepath = os.path.join(directory, file_entry.name)
            
            # read json
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    data = json.load(f)
            except Exception as e:
                self.report({'ERROR'}, f"Failed to read {file_entry.name}: {e}")
                continue

            parsed = parse_playcanvas_data(data)
            file_display_name = bpy.path.display_name_from_filepath(filepath) or "PlayCanvas_Model"

            root = bpy.data.objects.new(file_display_name, None)
            root.empty_display_type = 'PLAIN_AXES'
            target_collection.objects.link(root)

            world_matrices = compute_world_matrices(parsed["nodes"]) if parsed["nodes"] else {}

            # 노드 생성
            for node in parsed["nodes"]:
                empty = bpy.data.objects.new(node["name"], None)
                empty.empty_display_size = 0.1
                empty.matrix_world = conv_mat @ world_matrices.get(node["index"], Matrix())
                empty.parent = root
                target_collection.objects.link(empty)

            # 메티리얼 생성
            material_map = {}
            base_dir = os.path.dirname(filepath)
            for mat_info in parsed["materials"]:
                mat = bpy.data.materials.new(mat_info["name"])
                mat.use_nodes = True
                bsdf = mat.node_tree.nodes.get("Principled BSDF")
                
                if mat_info.get("diffuse_map"):
                    tex_path = os.path.join(base_dir, mat_info["diffuse_map"])
                    load_texture(mat, tex_path)
                elif mat_info.get("diffuse_color") and len(mat_info["diffuse_color"]) >= 3:
                    color = [c/255 for c in mat_info["diffuse_color"][:3]] + [1.0]
                    bsdf.inputs["Base Color"].default_value = color
                
                material_map[mat_info["index"]] = mat

            # 오브젝트 배치
            for inst in parsed["instances"]:
                mesh_idx = inst.get("mesh")
                node_idx = inst.get("node")
                mat_idx = inst.get("material")

                if mesh_idx is None or mesh_idx >= len(parsed["meshes"]):
                    continue
                mesh_data = parsed["meshes"][mesh_idx]
                if not mesh_data.get("positions"):
                    continue

                obj = build_mesh_object(
                    name=mesh_data["name"],
                    positions=mesh_data["positions"],
                    normals=mesh_data.get("normals"),
                    indices=mesh_data.get("indices"),
                    uv0=mesh_data.get("uv0"),
                    uv1=mesh_data.get("uv1"),
                )
                obj.matrix_world = conv_mat @ world_matrices.get(node_idx, Matrix())
                obj.parent = root
                target_collection.objects.link(obj)

                if mat_idx in material_map:
                    obj.data.materials.append(material_map[mat_idx])

        return {'FINISHED'}

def menu_func_import(self, context):
    self.layout.operator(ImportPlayCanvas.bl_idname, text="PlayCanvas (.json)")

classes = (ImportPlayCanvas,)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)

def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
