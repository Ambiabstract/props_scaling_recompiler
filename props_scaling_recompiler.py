import re
import os
import sys
import subprocess
import shutil
import argparse
import io
import time
from colorama import init, Fore
import pickle

debug_mode = False

# Regular expression for deleting ANSI escape sequences
ansi_escape = re.compile(r'\x1B[@-_][0-?]*[ -/]*[@-~]')

ccld_url = r"https://github.com/UltraTechX/Crowbar-Command-Line/releases/latest"
vpkedit_url = r"https://github.com/craftablescience/VPKEdit/releases/latest"
crowbar_appdata_settings = r"%appdata%\ZeqMacaw"
extracted_vpks_folder_name = f"props_scaling_recompiler_temp_vpk_content"

log_buffer = io.StringIO()

def print_and_log(*args, **kwargs):
    message = ' '.join(map(str, args))
    print(message + Fore.RESET, **kwargs)
    log_message = ansi_escape.sub('', message)
    log_buffer.write(log_message + '\n')

def get_script_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

def get_script_name():
    if getattr(sys, 'frozen', False):
        script_name = os.path.basename(sys.executable)
    else:
        script_name = os.path.basename(os.path.abspath(__file__))
    return os.path.splitext(script_name)[0]

def add_to_cache(psr_cache_data, model, modelscale, rendercolor, skin, real_mdl_path=None, is_static=False):
    model = model.lower()
    
    if model not in psr_cache_data:
        psr_cache_data[model] = {
            "scales": [],
            "colors": [],
            "real_mdl_path": real_mdl_path,
            "is_static": is_static
        }
    else:
        if real_mdl_path is not None and psr_cache_data[model].get("real_mdl_path") != real_mdl_path:
            psr_cache_data[model]["real_mdl_path"] = real_mdl_path

        if psr_cache_data[model].get("is_static") != is_static:
            psr_cache_data[model]["is_static"] = is_static
            #print_and_log(Fore.YELLOW + f"ATTENTION! is_static changed:")
            #print_and_log(Fore.YELLOW + f"model: {model}")
            #print_and_log(Fore.YELLOW + f"model: {is_static}")

    if modelscale not in psr_cache_data[model]["scales"]:
        psr_cache_data[model]["scales"].append(modelscale)
    
    if len(psr_cache_data[model]["colors"]) < 31:
        if [[rendercolor], [skin]] not in psr_cache_data[model]["colors"]:
            psr_cache_data[model]["colors"].append([[rendercolor], [skin]])
    else:
        print_and_log(Fore.RED + f"ERROR! {get_file_name(model)}.mdl has too many skins, can't add another color!")
    
    return psr_cache_data

def remove_from_cache(psr_cache_data, model, modelscales_to_remove=None, rendercolors_to_remove=None, skins_to_remove=None, remove_real_mdl_path=False, remove_is_static=False):
    if model not in psr_cache_data:
        print_and_log(f"Model {model} not found in cache!")
        return psr_cache_data

    if modelscales_to_remove:
        original_scales = psr_cache_data[model].get('scales', [])
        psr_cache_data[model]['scales'] = [scale for scale in original_scales if scale not in modelscales_to_remove]
        print_and_log(f"Removed scales {modelscales_to_remove} from {model}. Remaining scales: {psr_cache_data[model]['scales']}")

    if rendercolors_to_remove or skins_to_remove:
        original_colors = psr_cache_data[model].get('colors', [])
        psr_cache_data[model]['colors'] = [
            color_pair for color_pair in original_colors
            if not (
                (rendercolors_to_remove and color_pair[0][0] in rendercolors_to_remove) or
                (skins_to_remove and color_pair[1][0] in skins_to_remove)
            )
        ]
        print_and_log(f"Removed specified colors/skins from {model}. Remaining colors: {psr_cache_data[model]['colors']}")

    if remove_real_mdl_path and 'real_mdl_path' in psr_cache_data[model]:
        del psr_cache_data[model]['real_mdl_path']
        print_and_log(f"Removed real_mdl_path from {model}.")

    if remove_is_static and 'is_static' in psr_cache_data[model]:
        del psr_cache_data[model]['is_static']
        print_and_log(f"Removed is_static from {model}.")
    
    return psr_cache_data

def check_psr_data(psr_cache_data_check, psr_cache_data_ready):
    #print_and_log(f"                                 ")
    #print_and_log(f"CHECK PSR DATA:")
    #print_and_log(f"psr_cache_data_check: {psr_cache_data_check}")
    #print_and_log(f"psr_cache_data_ready: {psr_cache_data_ready}")
    #print_and_log(f"                                 ")

    for model, model_data in psr_cache_data_check.items():
        if model not in psr_cache_data_ready:
            return False

        scales_check = set(model_data.get('scales', []))
        scales_ready = set(psr_cache_data_ready[model].get('scales', []))
        if not scales_check.issubset(scales_ready):
            return False

        colors_check = model_data.get('colors', [])
        colors_ready = psr_cache_data_ready[model].get('colors', [])
        if not any(color_check in colors_ready for color_check in colors_check):
            return False
    return True

def save_global_cache(psr_cache_data_ready):
    with open('props_scaling_recompiler_cache.pkl', 'wb') as f:
        pickle.dump(psr_cache_data_ready, f)
        print_and_log(f"Cache saved.")

def load_global_cache():
    if os.path.exists('props_scaling_recompiler_cache.pkl'):
            with open('props_scaling_recompiler_cache.pkl', 'rb') as f:
                psr_cache_data_ready = pickle.load(f)
                return psr_cache_data_ready
    else:
        return None

def process_vmf(game_dir, file_path, psr_cache_data_ready, force_recompile=False, classnames = ["prop_static_scalable", "prop_dynamic_scalable", "prop_physics_scalable"]):
    entities_raw = []
    entities_ready = []
    entities_todo = []
    psr_cache_data_raw = {}
    psr_cache_data_todo = {}
    
    with open(file_path, 'r') as file:
        content = file.read()

    classnames_pattern = '|'.join(classnames)

    pattern_old_fgd = re.compile(
        rf'entity\s*\{{'
        rf'[^\{{}}]*"id"\s*"(?P<id>\d+)"\s*'
        rf'[^\{{}}]*"classname"\s*\"(?P<classname>{classnames_pattern})\"\s*'
        rf'[^\{{}}]*"model"\s*"(?P<model>[^"]+)"\s*'
        rf'[^\{{}}]*"modelscale"\s*"(?P<modelscale>[^"]+)"\s*',
        re.DOTALL | re.MULTILINE
    )
    matches_old_fgd = list(pattern_old_fgd.finditer(content))
    
    entities_matches_old_fgd_len = len(matches_old_fgd)
    
    pattern = re.compile(
        rf'entity\s*\{{'
        rf'[^\{{}}]*"id"\s*"(?P<id>\d+)"\s*'
        rf'[^\{{}}]*"classname"\s*\"(?P<classname>{classnames_pattern})\"\s*'
        rf'[^\{{}}]*"model"\s*"(?P<model>[^"]+)"\s*'
        rf'[^\{{}}]*"modelscale"\s*"(?P<modelscale>[^"]+)"\s*'
        # "rendercolor" "222 22 22"
        rf'[^\{{}}]*"rendercolor"\s*"(?P<rendercolor>[^"]+)"\s*'
        #"skin" "0"
        rf'[^\{{}}]*"skin"\s*"(?P<skin>[^"]+)"\s*'
        rf'[^\{{}}]*"origin"\s*"(?P<origin>[^"]+)"\s*',
        re.DOTALL | re.MULTILINE
    )
    matches = list(pattern.finditer(content))
    
    entities_matches_len = len(matches)
    
    if entities_matches_old_fgd_len == 0:
            print_and_log(f"No prop_static_scalable entities found.")
            return entities_raw, entities_ready, entities_todo, psr_cache_data_raw, psr_cache_data_ready, psr_cache_data_todo
    
    if entities_matches_len < entities_matches_old_fgd_len:
        print_and_log(f" ")
        print_and_log(Fore.RED + f"ERROR: old entities KeyValues detected!")
        print_and_log(Fore.YELLOW + f"Please update the FGD file to new version, restart the Hammer++ and save your map (that save will update entities KeyValues).")
        print_and_log(Fore.YELLOW + f"It is required for a work of the new version of the tool.")
        print_and_log(Fore.YELLOW + f"Props will not be scaled!")
        print_and_log(f" ")
        input("Press any key to continue.")
        return entities_raw, entities_ready, entities_todo, psr_cache_data_raw, psr_cache_data_ready, psr_cache_data_todo

    '''
    if os.path.exists('props_scaling_recompiler_cache.pkl'):
        with open('props_scaling_recompiler_cache.pkl', 'rb') as f:
            psr_cache_data_ready = pickle.load(f)
    '''
    psr_cache_data_ready_load = load_global_cache()
    if psr_cache_data_ready_load != None: psr_cache_data_ready = psr_cache_data_ready_load
    
    print_and_log(f" ")
    print_and_log(f"{entities_matches_len} prop_static_scalable entities found.")
    print_and_log(f"Reading VMF, please wait...")

    entities_matches_progress = 0
    for match in matches:
        print(f"Progress: {int(entities_matches_progress*100/entities_matches_len)}%", end="\r")
        entities_matches_progress += 1

        if debug_mode: print_and_log(f"                ")
        
        entity_id = match.group('id')
        if debug_mode: print_and_log(f"id: {entity_id}")
        
        classname = match.group('classname')
        if debug_mode: print_and_log(f"classname: {classname}")
        
        model = match.group('model')
        if debug_mode: print_and_log(f"model: {model}")
        
        origin = match.group('origin')
        #if debug_mode: print_and_log(f"origin: {origin}")
        
        modelscale = match.group('modelscale')
        if debug_mode: print_and_log(f"modelscale: {modelscale}")
        if "," in modelscale:
            print_and_log(Fore.YELLOW + f"Warning! Model scale of {get_file_name(model)}.mdl has a comma! Entity ID: {entity_id}. Entity origin: '{origin}'. Compiling with scale 1.")
            modelscale = "1.0"

        if float(modelscale) < 0.01:
            print_and_log(Fore.RED + f"ERROR! {get_file_name(model)}.mdl has wrong scale: {modelscale}. Should be more than 0.01. Entity ID: {entity_id}. Entity origin: '{origin}'. Skipping!")
            continue

        # Funny fix
        modelscale = float(modelscale) 
        modelscale = str(modelscale)
        
        rendercolor = match.group('rendercolor')
        #global g_rendercolor
        #g_rendercolor = rendercolor
        
        #print_and_log(f"                                ")
        #print_and_log(f"242! rendercolor: {rendercolor}")
        
        skin = match.group('skin')
        #global g_skin
        #g_skin = skin
        #print_and_log(f"245! skin: {skin}")

        psr_cache_data_raw = add_to_cache(psr_cache_data_raw, model, modelscale, rendercolor, skin)
        #print_and_log(f"248! psr_cache_data_raw: {psr_cache_data_raw}")

        entity_dict = {
            "id": entity_id,
            "model": model,
            "modelscale": modelscale,
            "rendercolor": rendercolor,
            "skin": skin
        }
        
        entities_raw.append(entity_dict)

        if force_recompile:
            entities_todo.append(entity_dict)
            psr_cache_data_todo = psr_cache_data_raw
            continue
        else:
            if len(psr_cache_data_ready) != 0:
                psr_cache_data_empty = {}
                psr_cache_data_check = add_to_cache(psr_cache_data_empty, model, modelscale, rendercolor, skin)
                print_and_log(f"psr_cache_data_check: {psr_cache_data_check}")
                print_and_log(f"psr_cache_data_ready: {psr_cache_data_ready}")
                
                # Если собранная энтитя в psr_cache_data_check уже есть в глобальном кэше - добавляем в реди и нет смысла это компилить
                # вот тут надо проверять единичные статичные модели, должны попадать в реди, в прошлый раз ошибка была связана с тем что check_psr_data видит скейл 1 отличным от 1.0
                if check_psr_data(psr_cache_data_check, psr_cache_data_ready):
                    #entities_ready.append(entity_dict)
                    print_and_log(f"check_psr_data: True")
                    is_static = psr_cache_data_ready.get(model, {}).get("is_static", None)
                    #print_and_log(f"model: {model}")
                    #print_and_log(f"modelscale: {modelscale}")
                    #print_and_log(f"is_static from global cache: {is_static}")
                    #print_and_log(f"psr_cache_data_ready before add to cache: {psr_cache_data_ready}")
                    #if float(modelscale) == 1:
                    print_and_log(f"model: {model}")
                    print_and_log(f"rendercolor: {rendercolor}")
                    print_and_log(f"skin: {skin}")
                    #    print_and_log(f"is_static: {is_static}")
                    #    print_and_log(f"psr_cache_data_ready before add to cache: {psr_cache_data_ready}")
                    psr_cache_data_ready = add_to_cache(psr_cache_data_ready, model, modelscale, rendercolor, skin, is_static=is_static)
                    #print_and_log(f"psr_cache_data_ready after add to cache: {psr_cache_data_ready}")
                    #input("Всё в порядке (якобы)")
                    continue
                else:
                    print_and_log(f"check_psr_data: False")
                    #print_and_log(f" ")
                    #print_and_log(f"psr_cache_data_ready before add to cache: {psr_cache_data_ready}")
                    print_and_log(f" ")
                    print_and_log(f"model: {model}")
                    print_and_log(f"rendercolor: {rendercolor}")
                    print_and_log(f"skin: {skin}")
                    #print_and_log(f"modelscale: {modelscale}")
                    #is_static = psr_cache_data_ready.get(model, {}).get("is_static", None)
                    #print_and_log(f"is_static from global cache: {is_static}")
                    #input("Не всё в порядке")
            mdl_name = get_file_name(model)
            mdl_name_scaled = process_mdl_name(mdl_name, modelscale)
            mdl_scaled_path = find_mdl_file(game_dir, mdl_name_scaled)
            if mdl_scaled_path is None:
                entities_todo.append(entity_dict)
                psr_cache_data_todo = add_to_cache(psr_cache_data_todo, model, modelscale, rendercolor, skin)
                #print_and_log(f"304! psr_cache_data_todo: {psr_cache_data_todo}")
            #elif rendercolor is not f"255 255 255": #вот тут чота происходит непонятновое((((
            #    entities_todo.append(entity_dict)
            #    print_and_log(f"entity_dict: {entity_dict}                         ")
            #    print_and_log(f"rendercolor: '{rendercolor}'                         ")
            #    input("zfsfgh                         ")
            else:
                # Почему-то казалось что в реди нужно добавлять уже трансформированное имя, но это ошибка, финальное имя генерируется перед встраиванием в VMF
                '''
                entity_dict_ready = {
                    "id": entity_id,
                    "model": transform_mdl_path_to_hammer_style(mdl_scaled_path),
                    "modelscale": '1',
                    "rendercolor": rendercolor,
                    "skin": skin
                }
                '''
                #entities_ready.append(entity_dict_ready)
                #entities_ready.append(entity_dict)
                is_static = psr_cache_data_ready.get(model, {}).get("is_static", None)
                psr_cache_data_ready = add_to_cache(psr_cache_data_ready, model, modelscale, rendercolor, skin, is_static=is_static)
                #print_and_log(f"255! psr_cache_data_ready: {psr_cache_data_ready}")

    print_and_log(f"Progress: Done!")
    
    print_and_log(f" ")

    if force_recompile: print_and_log(Fore.YELLOW + f"Force recompile mode: scaled and static assets removing from project files...")
    if force_recompile and os.path.exists('props_scaling_recompiler_cache.pkl'):
        os.remove('props_scaling_recompiler_cache.pkl')
    if force_recompile: remove_vmf_assets(entities_raw, game_dir, remove_static=True)
    if force_recompile: print_and_log(f" ")

    print_and_log(f"{len(psr_cache_data_ready)} models in cache.")
    print_and_log(f"{len(psr_cache_data_raw)} original models in this VMF.")
    print_and_log(f"{len(entities_raw)} models variations in this VMF.")
    print_and_log(f"{len(psr_cache_data_todo)} models to recompile for this VMF.")
    print_and_log(f" ")

    save_global_cache(psr_cache_data_ready)
    
    return entities_raw, entities_ready, entities_todo, psr_cache_data_raw, psr_cache_data_ready, psr_cache_data_todo

def find_mdl_file(game_dir, mdl_name):
    mdl_filename = f"{mdl_name}.mdl"
    for root, dirs, files in os.walk(game_dir):
        if mdl_filename in files:
            full_path = os.path.join(root, mdl_filename)
            if full_path.startswith(os.path.join(game_dir, "models")):
                if debug_mode: print_and_log(f"[find_mdl_file] {mdl_name}.mdl full_path: {full_path}")
                return full_path
            else:
                models_index = full_path.find("\\models\\")
                if models_index != -1:
                    mdl_path_custom = os.path.join(game_dir, full_path[models_index + 1:])
                    if debug_mode: print_and_log(f"Warning! {mdl_name}.mdl found in some custom folder!")
                    if debug_mode: print_and_log(f"[find_mdl_file] {mdl_name}.mdl full_path: {full_path}")
                    if debug_mode: print_and_log(f"[find_mdl_file] Hammer {mdl_name}.mdl path: {transform_mdl_path_to_hammer_style(mdl_path_custom)}")
                    return mdl_path_custom
    return None

def find_real_mdl_path(game_dir, hammer_mdl_path):
    hammer_mdl_path = os.path.normpath(hammer_mdl_path)
    hammer_parts = hammer_mdl_path.split(os.sep)
    
    if debug_mode: print_and_log(f"hammer_parts: {hammer_parts}")

    if "models" not in hammer_parts:
        print_and_log(Fore.RED + f"[find_real_mdl_path] ERROR! Path must contain 'models' directory")
        return None
    
    models_index = hammer_parts.index("models")
    hammer_dirs = hammer_parts[models_index:]
    
    if debug_mode: print_and_log(f"models_index: {models_index}")
    if debug_mode: print_and_log(f"hammer_dirs: {hammer_dirs}")
    
    mdl_filename = hammer_dirs[-1]
    hammer_dirs = hammer_dirs[:-1]

    excluded_dirs = [".git", "sound", "scripts", "modelsrc", "screenshots", "media", "materials"]
    
    for root, dirs, files in os.walk(game_dir):
        dirs[:] = [d for d in dirs if d not in excluded_dirs]
        rel_path = os.path.relpath(root, game_dir)
        rel_parts = rel_path.split(os.sep)
        if debug_mode: print_and_log(f"rel_path: {rel_path}, rel_parts: {rel_parts}")
        if rel_parts[-len(hammer_dirs):] == hammer_dirs:
            if mdl_filename in files:
                return os.path.join(root, mdl_filename)
    
    return None

def find_real_vmt_path(game_dir, material_path):
    material_path = os.path.normpath(material_path)
    material_path_parts = material_path.split(os.sep)
    
    print_and_log(f" ")
    print_and_log(f"material_path_parts: {material_path_parts}")

    if "materials" not in material_path_parts:
        print_and_log(Fore.RED + f"[find_real_vmt_path] ERROR! Path must contain 'materials' directory")
        return None
    
    materials_index = material_path_parts.index("materials")
    hammer_dirs = material_path_parts[materials_index:]
    
    print_and_log(f" ")
    print_and_log(f"materials_index: {materials_index}")
    print_and_log(f" ")
    print_and_log(f"hammer_dirs: {hammer_dirs}")
    
    vmt_filename = hammer_dirs[-1]
    hammer_dirs = hammer_dirs[:-1]

    excluded_dirs = [".git", "sound", "scripts", "modelsrc", "screenshots", "media"]
    
    for root, dirs, files in os.walk(game_dir):
        dirs[:] = [d for d in dirs if d not in excluded_dirs]
        rel_path = os.path.relpath(root, game_dir)
        rel_parts = rel_path.split(os.sep)
        #print_and_log(f"rel_path: {rel_path}, rel_parts: {rel_parts}")
        if rel_parts[-len(hammer_dirs):] == hammer_dirs:
            if vmt_filename in files:
                return os.path.join(root, vmt_filename)
    
    return None

def get_file_name(file_path):
    file_name_with_extension = os.path.basename(file_path)
    file_name = os.path.splitext(file_name_with_extension)[0]
    return file_name

def transform_mdl_path_to_hammer_style(full_path):
    full_path_lower = full_path.lower()
    pattern = r"[\\/](models)[\\/]"
    match = re.search(pattern, full_path_lower)
    
    if not match:
        print_and_log(Fore.RED + f"ERROR! Cant convert path to hammer style: {full_path}")
        print_and_log(Fore.RED + f"'models' folder not found in that path.")
        return None

    # Cut everything down to the “models” folder and replace the slashes with “/”
    models_index = match.start()
    relative_path = full_path[models_index + 1:]
    unix_style_path = re.sub(r'[\\/]', '/', relative_path)
    return unix_style_path

def process_mdl_name(mdl_name, modelscale):
    if "_scaled_" in mdl_name:
        parts = mdl_name.split("_scaled_")
        scale_from_name = float(parts[1]) / 100
        modelscale = scale_from_name * float(modelscale)
        base_name = parts[0]
    else:
        base_name = mdl_name
        if float(modelscale) == 1.0:
            new_mdl_name = f"{base_name}_static"
            return new_mdl_name

    modelscale = float(modelscale) * 100
    modelscale = int(modelscale)
    if modelscale == 100:
        new_mdl_name = f"{base_name}"
    else:
        new_mdl_name = f"{base_name}_scaled_{modelscale}"

    return new_mdl_name

def remove_all_scaled_files(game_dir):
    for root, dirs, files in os.walk(game_dir):
        for file in files:
            if "_scaled_" in file and file.endswith(('.vtx', '.mdl', '.phy', '.vvd')):
                file_path = os.path.join(root, file)
                os.remove(file_path)
                if debug_mode: print_and_log(f"Scaled file removed: {file_path}")

def remove_scaled_files(game_dir, mdl_name, remove_static=False):
    mdl_name = mdl_name.lower()
    for root, dirs, files in os.walk(game_dir):
        for file in files:
            file_lower = file.lower()
            if mdl_name in file_lower:
                if "_scaled_" in file_lower and file_lower.endswith(('.vtx', '.mdl', '.phy', '.vvd')):
                    file_path = os.path.join(root, file)
                    os.remove(file_path)
                    if debug_mode: print_and_log(f"Scaled file removed: {file_path}")
                elif remove_static:
                    if "_static" in file_lower and file_lower.endswith(('.vtx', '.mdl', '.phy', '.vvd')):
                        file_path = os.path.join(root, file)
                        os.remove(file_path)
                        if debug_mode: print_and_log(f"Static file removed: {file_path}")


def remove_vmf_assets(entities_raw, game_dir, remove_static=False):
    entities_raw_len = len(entities_raw)
    entities_raw_progress = 0
    
    for entity in entities_raw:
        if debug_mode: print_and_log(f"[remove_vmf_assets] entity: {entity}")
        model = entity['model']
        if debug_mode: print_and_log(f"[remove_vmf_assets] model: {model}")
        mdl_name = get_file_name(model)
        if debug_mode: print_and_log(f"[remove_vmf_assets] mdl_name: {mdl_name}")
        remove_scaled_files(game_dir, mdl_name, remove_static)
        
        entities_raw_progress += 1
        
        if entities_raw_progress >= entities_raw_len:
            print_and_log(f"Progress: Done!")
        else:
            print(f"Progress: {int(entities_raw_progress*100/entities_raw_len)}%", end="\r")

def run_ccld(mdl_path, ccld_path, decomp_folder):
    print_and_log(f"\nDecompilation started with CrowbarCommandLineDecomp:\n")
    try:
        command = f'"{ccld_path}" -p "{mdl_path}" -o "{decomp_folder}"'
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            if debug_mode: print_and_log(f"\nEnd of decompilation")
            #print_and_log(f"CrowbarCommandLineDecomp out: {result.stdout}")
        else:
            print_and_log(Fore.RED + f"\nERROR decompilation!")
            #print_and_log(f"CrowbarCommandLineDecomp Error: {result.stderr}")
    
    except Exception as e:
        print_and_log(Fore.RED + f"ERROR: {e}")

def compile_model(compiler_path, game_folder, qc_path, hammer_mdl_path, scale, rendercolor, skin, psr_cache_data_todo, psr_cache_data_ready):
    command = [
        compiler_path,
        "-game", game_folder,
        "-nop4",
        "-verbose",
        qc_path
    ]

    try:
        result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print_and_log("Output:", result.stdout.decode())
        output = result.stdout.decode('utf-8')
        if f'Completed "{os.path.basename(qc_path)}"' in output:
            is_static = psr_cache_data_ready.get(hammer_mdl_path, {}).get("is_static", None)
            psr_cache_data_ready = add_to_cache(psr_cache_data_ready, hammer_mdl_path, scale, rendercolor, skin, is_static=is_static)
            save_global_cache(psr_cache_data_ready)
        #else:
        #    print_and_log(Fore.RED + f"Model compilation failed!")
    except subprocess.CalledProcessError as e:
        print_and_log(Fore.RED + f"Model compilation failed! An error occurred: {e}")
        print_and_log("Output:", e.stdout.decode())
        #print_and_log("Errors:", e.stderr.decode())

def fix_phys_collision_smd(qc_path):
    try:
        with open(qc_path, 'r', encoding='utf-8') as qc_file:
            lines = qc_file.readlines()

        collision_model_line = None
        for line in lines:
            if '$collisionmodel' in line:
                collision_model_line = line.strip()
                break
        
        if not collision_model_line:
            print_and_log(Fore.RED + f"Error: The string $collisionmodel is not found in the QC file: {qc_path}")
            return False

        smd_path = collision_model_line.split('"')[1]
        smd_path = os.path.join(os.path.dirname(qc_path), smd_path)

        if not os.path.exists(smd_path):
            print_and_log(Fore.RED + f"Error: SMD file {smd_path} was not found.")
            return False

        with open(smd_path, 'r', encoding='utf-8') as smd_file:
            smd_lines = smd_file.readlines()

        skeleton_found = False
        for i, line in enumerate(smd_lines):
            if 'skeleton' in line.strip():
                skeleton_found = True
            if skeleton_found and 'time 0' in line.strip():
                parts = smd_lines[i + 1].strip().split()
                if len(parts) == 7:
                    #1.570796
                    #0.000000
                    parts[4] = str(float(parts[4]) * 2)
                    parts[5] = str(float(parts[5]) * 2)
                    parts[6] = str(float(parts[6]) * 2)
                    #parts[4] = str(float(parts[4]) + 0)
                    #parts[5] = str(float(parts[5]) + 1.570796)
                    #parts[6] = str(float(parts[6]) + 0)
                    smd_lines[i + 1] = '    ' + ' '.join(parts) + '\n'
                break

        with open(smd_path, 'w', encoding='utf-8') as smd_file:
            smd_file.writelines(smd_lines)

        return True

    except Exception as e:
        print_and_log(Fore.RED + f"ERROR: {e}")
        return False

def rescale_qc_file(qc_path, scale, hammer_mdl_path, psr_cache_data_todo, psr_cache_data_ready, convert_to_static=False, subfolders=True):
    prop_physics = False
    prop_dynamic = False
    prop_static = False
    new_qc_path = qc_path
    new_scale = scale
    new_hammer_modelname = None

    def scale_values(line, scale):
        numbers = re.findall(r"[-+]?\d*\.\d+|\d+", line)
        scaled_numbers = [str(float(num) * scale) for num in numbers]
        parts = re.split(r"([-+]?\d*\.\d+|\d+)", line)
        new_line = ""
        for part in parts:
            if part in numbers:
                new_line += scaled_numbers.pop(0)
            else:
                new_line += part
        return new_line

    def comment_line(line):
        return f"// {line}"

    with open(qc_path, 'r') as file:
        lines = file.readlines()
        #content = file.read()

    staticprop_found = any("$staticprop" in line for line in lines)
    keyvalues_found = any("$keyvalues" in line for line in lines)
    prop_data_found = any("prop_data" in line for line in lines)
    scale_found = any("$scale" in line for line in lines)
    
    if debug_mode: print_and_log(Fore.YELLOW + f"qc_path: {qc_path}")
    if debug_mode: print_and_log(Fore.YELLOW + f"staticprop_found: {staticprop_found}")
    if debug_mode: print_and_log(Fore.YELLOW + f"keyvalues_found: {keyvalues_found}")
    if debug_mode: print_and_log(Fore.YELLOW + f"prop_data_found: {prop_data_found}")
    if debug_mode: print_and_log(Fore.YELLOW + f"scale_found: {scale_found}")

    #if not staticprop_found:
    #    cls_fixed = fix_phys_collision_smd(qc_path)
    #    if debug_mode: print_and_log(Fore.YELLOW + f"cls_fixed: {cls_fixed}")
    
    # Fucking shit because of the bug that physics props for some reason scales not by N times, but by N^2 times unlike statics and some(!) dyn models
    scale_multi = scale
    if prop_data_found and not staticprop_found:
        scale_multi = scale ** 2

    modelname_line = ""
    modelname_index = -1
    scale_line_index = -1
    staticprop_line_index = -1

    for index, line in enumerate(lines):
        if line.strip().startswith("$modelname"):
            modelname_line = line.strip()
            modelname_index = index
        if line.strip().startswith("$scale"):
            scale_line_index = index
        if line.strip().startswith("$staticprop"):
            staticprop_line_index = index

    if modelname_line:
        parts = modelname_line.split('"')
        model_path = parts[1]
        model_name = os.path.basename(model_path).replace(".mdl", "")
        
        if debug_mode: print_and_log(Fore.YELLOW + f"!!! model_name: {model_name}")
        if debug_mode: print_and_log(Fore.YELLOW + f"!!! float(scale): {float(scale)}")

        if subfolders == True and float(scale) != 1.0:
            if debug_mode: print_and_log(Fore.YELLOW + f"!!! subfolders == True and float(scale) != 1.0")
            new_model_name = f"scaled/{model_name}_scaled_{int(scale * 100)}.mdl"
        else:
            if float(scale) == 1.0 and staticprop_found == False:
                if debug_mode: print_and_log(Fore.YELLOW + f"!!! float(scale) != 1.0 and staticprop_found == False")
                new_model_name = f"{model_name}_static.mdl"
            elif float(scale) == 1.0:
                if debug_mode: print_and_log(Fore.YELLOW + f"!!! float(scale) != 1.0")
                new_model_name = f"_do_not_compile_me!"
                print_and_log(Fore.GREEN + f"{model_name}.mdl is already a static prop. Updating cache.")
                #real_mdl_path = psr_cache_data_ready.get(hammer_mdl_path, {}).get("real_mdl_path")
                psr_cache_data_ready = add_to_cache(psr_cache_data_ready, hammer_mdl_path, modelscale="1.0", rendercolor="255 255 255", skin="0", is_static=True)
                save_global_cache(psr_cache_data_ready)
                #print_and_log(f"psr_cache_data_ready: {psr_cache_data_ready}")
                return f"static_prop"
            else:
                if debug_mode: print_and_log(Fore.YELLOW + f"!!! blyat")
                new_model_name = f"{model_name}_scaled_{int(scale * 100)}.mdl"
        if debug_mode: print_and_log(f"new_model_name: {new_model_name}")
        new_model_path = model_path.replace(f"{model_name}.mdl", new_model_name)
        if debug_mode: print_and_log(f"new_model_path: {new_model_path}")
        new_modelname_line = f'$modelname "{new_model_path}"\n'

        lines[modelname_index] = new_modelname_line
        
        if scale_line_index != -1:
            lines[scale_line_index] = f"$scale {scale_multi}\n"
        else:
            lines.insert(modelname_index + 1, '\n')
            lines.insert(modelname_index + 2, f"$scale {scale_multi}\n")
            scale_line_index = modelname_index + 2

        if staticprop_line_index == -1:
            lines.insert(scale_line_index + 1, "$staticprop\n")

        for index, line in enumerate(lines):
            if line.strip().startswith(("$lod")):
                lines[index] = scale_values(line, scale_multi)
            if line.strip().startswith(("$bbox", "$cbox", "$illumposition")):
                lines[index] = comment_line(line)
            if line.strip().startswith(("$definebone", "$hboxset")):
                lines[index] = comment_line(line)

        with open(qc_path, 'w') as file:
            file.writelines(lines)
    else:
        print_and_log(Fore.RED + f"$modelname not found in {model_name} QC!")

    return new_qc_path

def copy_and_rescale_qc(qc_path, scale, convert_to_static, subfolders, hammer_mdl_path, psr_cache_data_todo, psr_cache_data_ready):
    dir_name, file_name = os.path.split(qc_path)
    base_name, ext = os.path.splitext(file_name)
    new_file_name = f"{base_name}_scaled_{int(scale*100)}{ext}"
    new_qc_path = os.path.join(dir_name, new_file_name)
    shutil.copy(qc_path, new_qc_path)
    new_qc_path = rescale_qc_file(new_qc_path, scale, hammer_mdl_path, psr_cache_data_todo, psr_cache_data_ready, convert_to_static, subfolders)
    return new_qc_path

def model_painter(game_folder, qc_path, hammer_mdl_path, colors):
    with open(qc_path, 'r', encoding='utf-8') as file:
        content = file.read()
    
    #print_and_log(f" ")
    #print_and_log(f"lines:")
    #for line in lines:
    #    print_and_log(f"{line}")
    
    print_and_log(f" ")
    print_and_log(f"content:")
    print_and_log(f"{content}")
    
    print_and_log(f" ")
    print_and_log(f"qc_path: {qc_path}")
    
    studio_value = None
    match_smd = re.search(r'\$bodygroup\s+"[^"]+"\s*{[^}]*studio\s+"([^"]+)"', content, re.DOTALL)
    if match_smd:
        studio_value = match_smd.group(1)
     
    print_and_log(f" ")
    print_and_log(f"studio_value: {studio_value}")
    
    qc_folder = os.path.dirname(qc_path)
    smd_path = None
    if studio_value:
        smd_path = os.path.join(qc_folder, studio_value)
        smd_path = os.path.normpath(smd_path)
    
    print_and_log(f" ")
    print_and_log(f"smd_path: {smd_path}")
    
    unique_materials = None
    try:
        if not smd_path or not os.path.exists(smd_path):
            print_and_log(Fore.RED + f"Error: SMD file not found: {studio_value}")
            return None
        
        with open(smd_path, 'r', encoding='utf-8') as smd_file:
            smd_content = smd_file.read()
        print_and_log(f" ")
        print_and_log(f"smd_content:")
        print_and_log(f"{smd_content}")
        print_and_log(f" ")
        print_and_log(f"qc_path: {qc_path}")

        triangles_section = re.search(r'triangles(.*?)(end|$)', smd_content, re.DOTALL)
        if triangles_section:
            triangles_content = triangles_section.group(1)
            materials = re.findall(r'^\s*(\S+)\s*$', triangles_content, re.MULTILINE)
            unique_materials = list(set(materials))
        else:
            print_and_log(Fore.RED + f"Error: No triangles section found in the SMD file {studio_value}.")
    except FileNotFoundError:
        print_and_log(Fore.RED + f"Error: SMD file not found: {studio_value}")
        return None
    except Exception as e:
        print_and_log(Fore.RED + f"Error: {e}")
        return None
    
    first_material = None
    first_material_ext = None
    if unique_materials:
        first_material = unique_materials[0]
        first_material_ext = first_material + ".vmt"
    
    print_and_log(f" ")
    print_and_log(f"unique_materials: {unique_materials}")
    print_and_log(f" ")
    print_and_log(f"first_material: {first_material}")
    print_and_log(f" ")
    print_and_log(f"first_material_ext: {first_material_ext}")
    
    # тут надо получать из qc $cdmaterials "models\props\"

    def get_qc_cdmaterials(qc_path):
        with open(qc_path, 'r', encoding='utf-8') as f:
            qc_content = f.read()
        cdmaterials = re.findall(r'\$cdmaterials\s+"([^"]+)"', qc_content, re.MULTILINE)
        cdmaterials = [path.replace("\\", "/") for path in cdmaterials]
        return cdmaterials
    
    cdmaterials = get_qc_cdmaterials(qc_path)
    
    print_and_log(f" ")
    print_and_log(f"cdmaterials: {cdmaterials}")
    
    possible_materials_paths = [f"materials/{path}/{first_material_ext}" for path in cdmaterials]
    possible_materials_paths = [path.replace("//", "/") for path in possible_materials_paths]
    
    print_and_log(f" ")
    print_and_log(f"possible_materials_paths: {possible_materials_paths}")
    
    # аналоги функций:
    # find_real_mdl_path - поиск материала в файлах проекта                     - find_real_vmt_path
    # find_mdl_in_paths_from_gameinfo - поиск материала по папкам из гейминфо   - find_vmt_in_paths_from_gameinfo
    # extract_mdl - поиск по впк из гейминфо                                    - extract_vmt
    
    gameinfo_path = game_folder + f"/gameinfo.txt"
    
    script_path = get_script_path()
    all_source_engine_paths = os.path.abspath(os.path.join(script_path, ".."))
    search_paths = parse_search_paths(gameinfo_path)
    search_paths = search_paths_cleanup(search_paths, remove_gameinfo_path=False, remove_all_source_engine_paths=False, vmt = True)
    search_paths = update_search_paths(search_paths, game_folder, all_source_engine_paths)
    
    vpk_paths_from_gameinfo = only_vpk_paths_from_gameinfo(search_paths, vmt = True)
    #print_and_log(f" ")
    #print_and_log(f"vpk_paths_from_gameinfo:")
    #for path in vpk_paths_from_gameinfo:
    #    print_and_log(f"{path}")

    vpk_extract_folder = os.path.join(script_path, extracted_vpks_folder_name)
    vpkeditcli_path = os.path.join(script_path, "vpkeditcli.exe")
    
    real_vmt_paths = []
    
    for material_path in possible_materials_paths:
        print_and_log(f" ")
        print_and_log(f"material_path: {material_path}")
        real_vmt_path = find_real_vmt_path(game_folder, material_path)
        print_and_log(f" ")
        print_and_log(f"real_vmt_path: {real_vmt_path}")
        if real_vmt_path:
            # если нашёлся материал в папке с модом
            real_vmt_paths.append(real_vmt_path)
            continue
        else:
            # если не нашёлся материал в папке с модом - ищем в путях из гейминфо
            print_and_log(f" ")
            print_and_log(f"VMT not found in mod folder, trying to find in paths from gameinfo...")
            real_vmt_path = find_vmt_in_paths_from_gameinfo(search_paths, material_path)
            if real_vmt_path:
                print_and_log(f" ")
                print_and_log(f"real_vmt_path: {real_vmt_path}")
                real_vmt_paths.append(real_vmt_path)
                continue
            else:
                print_and_log(f" ")
                print_and_log(Fore.RED + f"real_vmt_path in found in paths from gameinfo!!!")
                print_and_log(f"Trying to find in VPKs...")
                # вот тут остановился
                real_vmt_path = extract_vmt(vpkeditcli_path, material_path, vpk_extract_folder, vpk_paths_from_gameinfo)
    if real_vmt_path:
        print_and_log(f" ")
        print_and_log(f"hammer_mdl_path: {hammer_mdl_path}")
        print_and_log(f"real_vmt_path: {real_vmt_path}")
    else:
        print_and_log(f" ")
        print_and_log(Fore.RED + f"real_vmt_path not found!!!!")
    
    qc_path_painted = qc_path
    return qc_path_painted

def rescale_and_compile_models(qc_path, compiler_path, game_folder, scales, convert_to_static, subfolders, hammer_mdl_path, psr_cache_data_todo, psr_cache_data_ready):    
    #print_and_log(f" ")
    #print_and_log(f"RESCALE AND COMPILE:")
    #print_and_log(f"scales: {scales}")
    #print_and_log(f" ")
    
    scales = list(set(map(float, scales.split())))
    scales.sort()

    print_and_log(f"psr_cache_data_todo: {psr_cache_data_todo}")
    print_and_log(f" ")
    colors = psr_cache_data_todo.get(hammer_mdl_path, {}).get("colors", None)
    print_and_log(f" ")
    print_and_log(f"colors: {colors}")

    qc_path_painted = model_painter(game_folder, qc_path, hammer_mdl_path, colors)
    
    # вот тут должен работать покрасчик
    # порядок действий:
    # добывать из raw или todo все rendercolor и все skin
    # прочитать оригинальный qc
    # найти в нём smd ref
    # прочитать smd ref, получить имя оригинального материала (набор материалов, но берём только первый)
    # найти оригинальный материал в контенте или впк
    # создать материалы с нужными параметрами
    # после этого добавить все комбинации сначала в оригинальный
    # и только после этого уже пойдёт копироваться набор qc для поскейленных

    # на вход идёт game_folder, qc_path и colors
    # в ней должны:
    # проверяться что только один материал используется
    # вычисляться все комбинации цветов и скинов
    # находиться этот самый материал (в том числе в впк надо искать)
    # клонирование и попутное редактирование материалов со всеми комбинациями
    
    # нужны аналоги функций для материала:
    # find_real_mdl_path - поиск в файлах проекта
    # find_mdl_in_paths_from_gameinfo - поиск по путям из гейминфо кроме впк
    # extract_mdl - поиск по путям из гейминфо в впк
    
    # g_search_paths - глобальная переменная где есть пути из гейминфо
    # да и вообще надо юзать глобальные переменные
    
    #print_and_log(f"g_rendercolor: {g_rendercolor}")
    #print_and_log(f"g_skin: {g_skin}")

    print_and_log(f" ")
    input("cheeeeeeeck")
    
    rendercolor = "255 255 255"
    skin = "0"

    for scale in scales:
        new_qc_path = copy_and_rescale_qc(qc_path, scale, convert_to_static, subfolders, hammer_mdl_path, psr_cache_data_todo, psr_cache_data_ready)
        if new_qc_path == None:
            print_and_log(Fore.YELLOW + f"Skip QC compiling (new_qc_path is none for some reason):\n{qc_path}")
        elif new_qc_path == "static_prop":
            print_and_log(f'Skip QC compiling, "{hammer_mdl_path}" is static prop and has scale 1.')
            pass
        else:
            compile_model(compiler_path, game_folder, new_qc_path, hammer_mdl_path, scale, rendercolor, skin, psr_cache_data_todo, psr_cache_data_ready)
            

def get_valid_path(prompt_message, valid_extension):
    while True:
        path = input(prompt_message).strip().strip('"')
        if os.path.isfile(path) and path.lower().endswith(valid_extension):
            return path
        else:
            print_and_log(Fore.RED + f"File not found, path is incorrect, or file does not have {valid_extension} extension. Try again.")

def decompile_dialog(mdl_path, ccld_path, hammer_mdl_path, psr_cache_data_todo, psr_cache_data_ready):    
    model_name = os.path.splitext(os.path.basename(mdl_path))[0]
    decomp_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mdl_scaler_decomp")
    #decomp_folder = r"C:\Code\PYTHON\PROP_STATIC_SCALABLE\props_scaling_recompiler_temp\decomp_folder_debug"
    decomp_folder = os.path.join(decomp_folder, model_name)
    if debug_mode: print_and_log(f"decomp_folder: {decomp_folder}")
    
    if os.path.exists(mdl_path):
        if debug_mode: print_and_log(f"mdl_path exist: {mdl_path}")
        if debug_mode: print_and_log(f"running decompilation...")
    else:
        print_and_log(Fore.RED + f"ERROR! mdl_path does not exist: {mdl_path}")
        #print_and_log(f"823 test! psr_cache_data_ready: {psr_cache_data_ready}")
        psr_cache_data_ready = remove_from_cache(psr_cache_data_ready, model=hammer_mdl_path, modelscales_to_remove=None, rendercolors_to_remove=None, skins_to_remove=None, remove_real_mdl_path=mdl_path)
        save_global_cache(psr_cache_data_ready)
        #print_and_log(f"825 test! psr_cache_data_ready: {psr_cache_data_ready}")
        return None
    
    run_ccld(mdl_path, ccld_path, decomp_folder)
    
    qc_path = decomp_folder + "/" + model_name + ".qc"
    if debug_mode: print_and_log(f"qc_path: {qc_path}")
    if os.path.isfile(qc_path) and qc_path.lower().endswith(".qc"):
        if debug_mode: print_and_log(f"qc_path is correct!")
        if debug_mode: print_and_log(f"\n")
        return qc_path
    else:
        print_and_log(Fore.RED + f"ERROR! qc_path is not correct: {qc_path}")

def check_bin_folder(script_path):
    folder_name = os.path.basename(script_path)

    if folder_name != "bin":
        print_and_log(Fore.RED + f"ERROR! This .exe file should lie in the bin folder where the Source Engine tools such as hammer.exe, studiomdl.exe and so on lie. For example:")
        print_and_log(Fore.RED + r"C:\Program Files (x86)\Steam\steamapps\common\Source SDK Base 2013 Singleplayer\bin")
        return False

    if not os.path.exists(os.path.join(script_path, "studiomdl.exe")):
        print_and_log(Fore.RED + "ERROR! I can't find studiomdl.exe in this bin folder! This .exe should be put in the bin folder with tools, not with client.dll and server.dll. For example:")
        print_and_log(Fore.RED + r"C:\Program Files (x86)\Steam\steamapps\common\Source SDK Base 2013 Singleplayer\bin")
        return False

    return True

def find_file(path, filename_ext):
    files_in_directory = os.listdir(path)
    if filename_ext in files_in_directory:
        return True
    else:
        return False

def find_file_in_subfolders(directory, filename_with_extension):
    result = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file == filename_with_extension:
                result.append(os.path.join(root, file))
    return result

def decompile_rescale_and_compile_model(ccld_path, gameinfo_path, compiler_path, mdl_path, scales, convert_to_static, subfolders, hammer_mdl_path, psr_cache_data_todo, psr_cache_data_ready):
    if debug_mode: print_and_log(f"ccld_path: {ccld_path}")
    if debug_mode: print_and_log(f"gameinfo_path: {gameinfo_path}")
    if debug_mode: print_and_log(f"compiler_path: {compiler_path}")
    if debug_mode: print_and_log(f"mdl_path: {mdl_path}")
    if debug_mode: print_and_log(f"scales: {scales}")
    qc_path = decompile_dialog(mdl_path, ccld_path, hammer_mdl_path, psr_cache_data_todo, psr_cache_data_ready)
    if qc_path is None: return
    if debug_mode: print_and_log(f"qc_path: {qc_path}")
    game_folder = gameinfo_path.rsplit('\\', 1)[0]
    if debug_mode: print_and_log(f"game_folder: {game_folder}")
    rescale_and_compile_models(qc_path, compiler_path, game_folder, scales, convert_to_static, subfolders, hammer_mdl_path, psr_cache_data_todo, psr_cache_data_ready)

def get_vpkeditcli_tree(vpkeditcli_path, vpk_file):
    result = subprocess.run(
        [vpkeditcli_path, '--file-tree', vpk_file],
        check=True,
        text=True,
        capture_output=True
    )
    return result.stdout, result.stderr

def extract_mdl(vpkeditcli_path, hammer_mdl_path, vpk_extract_folder, vpk_files):
    mdl_folder_path_orig = os.path.dirname(hammer_mdl_path)
    mdl_folder_path = mdl_folder_path_orig + r"/"

    mdl_name = os.path.splitext(os.path.basename(hammer_mdl_path))[0]
    mdl_name_with_ext = mdl_name + ".mdl"
    
    #mdl_parent_folder_name = os.path.basename(mdl_folder_path_orig)

    mdl_folder_path_without_name = mdl_folder_path.replace(f"{mdl_name}.mdl", '').strip(os.sep)
    
    mdl_folder_path_without_name_and_last_folder = '/'.join(mdl_folder_path_without_name.rstrip('/').split('/')[:-1]) + '/'

    if mdl_folder_path_without_name_and_last_folder == "/":
        mdl_folder_path_without_name_and_last_folder = ''
    
    vpk_extract_folder_model = os.path.join(os.path.join(get_script_path(), extracted_vpks_folder_name), mdl_folder_path_without_name_and_last_folder)
    vpk_extract_folder_model_with_last_folder = os.path.join(os.path.join(get_script_path(), extracted_vpks_folder_name), mdl_folder_path)
    
    if debug_mode: print_and_log(Fore.YELLOW + f"6. vpk_extract_folder_model: {vpk_extract_folder_model}")
    
    os.makedirs(vpk_extract_folder_model, exist_ok=True)
    os.makedirs(vpk_extract_folder_model_with_last_folder, exist_ok=True)

    vpk_with_mdl = None
    
    for vpk_file in vpk_files:
        try:
            vpkeditcli_tree_out, vpkeditcli_tree_err = get_vpkeditcli_tree(vpkeditcli_path, vpk_file)
            #print_and_log(f"vpkeditcli_tree_out: {vpkeditcli_tree_out}")
            #print_and_log(f"vpkeditcli_tree_err: {vpkeditcli_tree_err}")
            
            #ебать это днище, но по другому может быть ошибка
            mat_folder = "materials/" + mdl_folder_path_orig
            vpkeditcli_tree_out = vpkeditcli_tree_out.replace(mat_folder, '')            
            
            #if mdl_name_with_ext in vpkeditcli_tree_out:
            if mdl_folder_path_orig in vpkeditcli_tree_out and mdl_name_with_ext in vpkeditcli_tree_out:
                
                vpkeditcli_tree_out = vpkeditcli_tree_out.splitlines()
                
                folder_check = False
                model_check = False
                # дополнительная проверка чтобы не выгрузить случайно модель не из того впк
                for line in vpkeditcli_tree_out:
                    # Проверяем наличие mdl_folder_path_orig
                    if mdl_folder_path_orig in line:
                        folder_check = True
                        #print_and_log(Fore.YELLOW + f"folder_check = True!")
                        #print_and_log(Fore.YELLOW + f"line: {line}")
                        continue
                    # Проверяем наличие mdl_name_with_ext после mdl_folder_path_orig
                    if folder_check and mdl_name_with_ext in line:
                        model_check = True
                        #print_and_log(Fore.YELLOW + f"model_check = True!")
                        #print_and_log(Fore.YELLOW + f"line: {line}")
                        continue
                    # Проверяем, что не достигли строки, начинающейся с "models/"
                    if line.startswith("models/"):
                        #print_and_log(f"line: {line}")
                        if folder_check and model_check:
                            vpk_with_mdl = vpk_file
                            break
                        else:
                            folder_check = False
                            model_check = False
                
                if folder_check and model_check:
                    vpk_with_mdl = vpk_file
                    break

                if debug_mode: print_and_log(f"mdl_name_with_ext: {mdl_name_with_ext}")
                if debug_mode: print_and_log(f"os.path.dirname(hammer_mdl_path): {os.path.dirname(hammer_mdl_path)}")
                if debug_mode: print_and_log(f"hammer_mdl_path: {hammer_mdl_path}")
                if debug_mode: print_and_log(f"mdl_folder_path: {mdl_folder_path}")
                if debug_mode: print_and_log(f"vpk_extract_folder_model_with_last_folder: {vpk_extract_folder_model_with_last_folder}")

        except subprocess.CalledProcessError as e:
            print_and_log(Fore.RED + f"Error executing vpkeditcli: {e}")
            return None

    if vpk_with_mdl != None:
        print_and_log(Fore.GREEN + f"vpk with {mdl_name}.mdl found:\n{vpk_with_mdl}")
        try:
            if debug_mode: print_and_log(Fore.YELLOW + f"Extracting {mdl_name}.mdl from vpk...")
            
            extract_paths = []
            extract_paths.append(mdl_folder_path + mdl_name + ".mdl")
            extract_paths.append(mdl_folder_path + mdl_name + ".dx80.vtx")
            extract_paths.append(mdl_folder_path + mdl_name + ".dx90.vtx")
            extract_paths.append(mdl_folder_path + mdl_name + ".sw.vtx")
            extract_paths.append(mdl_folder_path + mdl_name + ".vvd")
            extract_paths.append(mdl_folder_path + mdl_name + ".phy")
            
            if debug_mode: print_and_log(f"extract_paths: {extract_paths}")
            if debug_mode: print_and_log(f" ")
            
            for extract_path in extract_paths:
                if debug_mode: print_and_log(f"extract_path: {extract_path}")
                if debug_mode: print_and_log(f"vpk_extract_folder_model: {vpk_extract_folder_model}")
                
                if ".mdl" in extract_path:
                    vpk_extract_model_path = os.path.join(os.path.join(get_script_path(), extracted_vpks_folder_name), mdl_folder_path) + mdl_name + ".mdl"
                if ".dx80.vtx" in extract_path:
                    vpk_extract_model_path = os.path.join(os.path.join(get_script_path(), extracted_vpks_folder_name), mdl_folder_path) + mdl_name + ".dx80.vtx"
                if ".dx90.vtx" in extract_path:
                    vpk_extract_model_path = os.path.join(os.path.join(get_script_path(), extracted_vpks_folder_name), mdl_folder_path) + mdl_name + ".dx90.vtx"
                if ".sw.vtx" in extract_path:
                    vpk_extract_model_path = os.path.join(os.path.join(get_script_path(), extracted_vpks_folder_name), mdl_folder_path) + mdl_name + ".sw.vtx"
                if ".vvd" in extract_path:
                    vpk_extract_model_path = os.path.join(os.path.join(get_script_path(), extracted_vpks_folder_name), mdl_folder_path) + mdl_name + ".vvd"
                if ".phy" in extract_path:
                    vpk_extract_model_path = os.path.join(os.path.join(get_script_path(), extracted_vpks_folder_name), mdl_folder_path) + mdl_name + ".phy"

                if debug_mode: print_and_log(Fore.YELLOW + f"vpk_extract_model_path: {vpk_extract_model_path}")
                
                vpkeditcli_extract_result = subprocess.run([vpkeditcli_path, '--output', vpk_extract_model_path, '--extract', extract_path, vpk_with_mdl], check=True)
            
            #print_and_log(f"vpkeditcli_extract_result.stdout {vpkeditcli_extract_result.stdout}")
            #print_and_log(f"vpkeditcli_extract_result.stderr {vpkeditcli_extract_result.stderr}")
            
        except subprocess.CalledProcessError as e:
            print_and_log(Fore.RED + f"Error executing vpkeditcli: {e}")
            return None
    else:
        print_and_log(Fore.RED + f"vpk with {mdl_name}.mdl not found :(")
        return None
        
    extracted_mdl_path = find_file_in_subfolders(vpk_extract_folder_model, f"{mdl_name}.mdl")
    
    if debug_mode: print_and_log(Fore.YELLOW + f"7. extracted_mdl_path: {extracted_mdl_path}")

    extracted_mdl_path = extracted_mdl_path[0]
    if debug_mode: print_and_log(Fore.YELLOW + f"8. extracted_mdl_path: {extracted_mdl_path}")

    if os.path.isfile(extracted_mdl_path):
        if debug_mode: print_and_log(f"9. extracted_mdl_path: {extracted_mdl_path}")
        return extracted_mdl_path
    else:
        print_and_log(Fore.RED + f"Extracted {mdl_name}.mdl file not found in: {extracted_mdl_path}")
        return None

def extract_vmt(vpkeditcli_path, hammer_mdl_path, vpk_extract_folder, vpk_files):
    mdl_folder_path_orig = os.path.dirname(hammer_mdl_path)
    mdl_folder_path = mdl_folder_path_orig + r"/"
    
    '''
    print_and_log(f" ")
    print_and_log(f"vmt_folder_path_orig: {mdl_folder_path_orig}")
    print_and_log(f" ")
    print_and_log(f"vmt_folder_path: {mdl_folder_path}")
    '''

    mdl_name = os.path.splitext(os.path.basename(hammer_mdl_path))[0]
    mdl_name_with_ext = mdl_name + ".vmt"
    
    '''
    print_and_log(f" ")
    print_and_log(f"vmt_name: {mdl_name}")
    print_and_log(f" ")
    print_and_log(f"vmt_name_with_ext: {mdl_name_with_ext}")
    '''
    
    #mdl_parent_folder_name = os.path.basename(mdl_folder_path_orig)

    mdl_folder_path_without_name = mdl_folder_path.replace(f"{mdl_name}.mdl", '').strip(os.sep)
    
    mdl_folder_path_without_name_and_last_folder = '/'.join(mdl_folder_path_without_name.rstrip('/').split('/')[:-1]) + '/'

    if mdl_folder_path_without_name_and_last_folder == "/":
        mdl_folder_path_without_name_and_last_folder = ''
    
    '''
    print_and_log(f" ")
    print_and_log(f"vmt_folder_path_without_name: {mdl_folder_path_without_name}")
    print_and_log(f"vmt_folder_path_without_name_and_last_folder: {mdl_folder_path_without_name_and_last_folder}")
    '''
    
    vpk_extract_folder_model = os.path.join(os.path.join(get_script_path(), extracted_vpks_folder_name), mdl_folder_path_without_name_and_last_folder)
    vpk_extract_folder_model_with_last_folder = os.path.join(os.path.join(get_script_path(), extracted_vpks_folder_name), mdl_folder_path)
    
    '''
    print_and_log(f" ")
    print_and_log(f"vpk_extract_folder_model: {vpk_extract_folder_model}")
    print_and_log(f" ")
    print_and_log(f"vpk_extract_folder_model_with_last_folder: {vpk_extract_folder_model_with_last_folder}")
    '''
    
    os.makedirs(vpk_extract_folder_model, exist_ok=True)
    os.makedirs(vpk_extract_folder_model_with_last_folder, exist_ok=True)

    vpk_with_mdl = None
    
    for vpk_file in vpk_files:
        try:
            vpkeditcli_tree_out, vpkeditcli_tree_err = get_vpkeditcli_tree(vpkeditcli_path, vpk_file)
            #print_and_log(f"vpkeditcli_tree_out: {vpkeditcli_tree_out}")
            #print_and_log(f"vpkeditcli_tree_err: {vpkeditcli_tree_err}")

            #if mdl_name_with_ext in vpkeditcli_tree_out:
            if mdl_folder_path_orig in vpkeditcli_tree_out and mdl_name_with_ext in vpkeditcli_tree_out:
                
                vpkeditcli_tree_out = vpkeditcli_tree_out.splitlines()
                
                folder_check = False
                model_check = False
                # дополнительная проверка чтобы не выгрузить случайно модель не из того впк
                for line in vpkeditcli_tree_out:
                    # Проверяем наличие mdl_folder_path_orig
                    if mdl_folder_path_orig in line:
                        folder_check = True
                        #print_and_log(Fore.YELLOW + f"folder_check = True!")
                        #print_and_log(Fore.YELLOW + f"line: {line}")
                        continue
                    # Проверяем наличие mdl_name_with_ext после mdl_folder_path_orig
                    if folder_check and mdl_name_with_ext in line:
                        model_check = True
                        #print_and_log(Fore.YELLOW + f"model_check = True!")
                        #print_and_log(Fore.YELLOW + f"line: {line}")
                        continue
                    # Проверяем, что не достигли строки, начинающейся с "models/"
                    if line.startswith("materials/"):
                        #print_and_log(f"line: {line}")
                        if folder_check and model_check:
                            vpk_with_mdl = vpk_file
                            break
                        else:
                            folder_check = False
                            model_check = False
                
                if folder_check and model_check:
                    vpk_with_mdl = vpk_file
                    break

                print_and_log(f"1263 big check")
                print_and_log(f"mdl_name_with_ext: {mdl_name_with_ext}")
                print_and_log(f"os.path.dirname(hammer_mdl_path): {os.path.dirname(hammer_mdl_path)}")
                print_and_log(f"hammer_mdl_path: {hammer_mdl_path}")
                print_and_log(f"mdl_folder_path: {mdl_folder_path}")
                print_and_log(f"vpk_extract_folder_model_with_last_folder: {vpk_extract_folder_model_with_last_folder}")
            '''
            else:
                print_and_log(f" ")
                print_and_log(Fore.RED + f"Material not found in VPK: {vpk_file}")
                print_and_log(f"Info:")
                print_and_log(f"vmt_folder_path_orig: {mdl_folder_path_orig}")
                print_and_log(f"vmt_name_with_ext: {mdl_name_with_ext}")
            '''

        except subprocess.CalledProcessError as e:
            print_and_log(Fore.RED + f"Error executing vpkeditcli: {e}")
            return None

    if vpk_with_mdl != None:
        print_and_log(Fore.GREEN + f"vpk with {mdl_name}.vmt found:\n{vpk_with_mdl}")
        try:
            if debug_mode: print_and_log(Fore.YELLOW + f"Extracting {mdl_name}.vmt from vpk...")
            
            extract_paths = []
            extract_paths.append(mdl_folder_path + mdl_name + ".vmt")
            
            if debug_mode: print_and_log(f"extract_paths: {extract_paths}")
            if debug_mode: print_and_log(f" ")
            
            for extract_path in extract_paths:
                if debug_mode: print_and_log(f"extract_path: {extract_path}")
                if debug_mode: print_and_log(f"vpk_extract_folder_model: {vpk_extract_folder_model}")
                
                if ".vmt" in extract_path:
                    vpk_extract_model_path = os.path.join(os.path.join(get_script_path(), extracted_vpks_folder_name), mdl_folder_path) + mdl_name + ".vmt"

                print_and_log(Fore.YELLOW + f"vpk_extract_model_path: {vpk_extract_model_path}")
                
                vpkeditcli_extract_result = subprocess.run([vpkeditcli_path, '--output', vpk_extract_model_path, '--extract', extract_path, vpk_with_mdl], check=True)
            
            #print_and_log(f"vpkeditcli_extract_result.stdout {vpkeditcli_extract_result.stdout}")
            #print_and_log(f"vpkeditcli_extract_result.stderr {vpkeditcli_extract_result.stderr}")
            
        except subprocess.CalledProcessError as e:
            print_and_log(Fore.RED + f"Error executing vpkeditcli: {e}")
            return None
    else:
        print_and_log(Fore.RED + f"VPK with {mdl_name}.vmt not found :(")
        return None
        
    extracted_mdl_path = find_file_in_subfolders(vpk_extract_folder_model, f"{mdl_name}.vmt")
    
    if debug_mode: print_and_log(Fore.YELLOW + f"7. extracted_mdl_path: {extracted_mdl_path}")

    extracted_mdl_path = extracted_mdl_path[0]
    if debug_mode: print_and_log(Fore.YELLOW + f"8. extracted_mdl_path: {extracted_mdl_path}")

    if os.path.isfile(extracted_mdl_path):
        if debug_mode: print_and_log(f"9. extracted_mdl_path: {extracted_mdl_path}")
        return extracted_mdl_path
    else:
        print_and_log(Fore.RED + f"Extracted {mdl_name}.vmt file not found in: {extracted_mdl_path}")
        return None

def parse_search_paths(gameinfo_path):
    search_paths = []
    in_search_paths_block = False

    with open(gameinfo_path, 'r', encoding='utf-8') as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            if line.startswith("SearchPaths"):
                in_search_paths_block = True
                continue
            if in_search_paths_block and line.startswith("}"):
                break
            if in_search_paths_block:
                # Deleting comments
                line = line.split("//", 1)[0].strip()
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    mode, path_with_ending = parts
                    # If there's a “|”, add a “/” after the last “|”
                    if '|' in path_with_ending:
                        path_with_ending = path_with_ending.rsplit('|', 1)[0] + '|/' + path_with_ending.rsplit('|', 1)[1]
                    # Check for an ending right after the folder
                    if path_with_ending.endswith('.') or path_with_ending.endswith('*'):
                        path, ending = path_with_ending[:-1], path_with_ending[-1]
                    elif '/' in path_with_ending:
                        path, ending = path_with_ending.rsplit('/', 1)
                    else:
                        path, ending = path_with_ending, ''
                    # Remove the slash at the end of the path, if there is one
                    if path.endswith('/'):
                        path = path[:-1]
                    search_paths.append((mode, path, ending))

    if debug_mode:
        print_and_log(Fore.YELLOW + f'Holy shit its search_paths!')
        for mode, path, ending in search_paths:
            print_and_log(f'{mode}\t{path}\t{ending}')
    
    # Update the search_paths list by removing the quotation marks
    search_paths = [(mode, path.replace('"', ''), ending.replace('"', '')) for mode, path, ending in search_paths]
    
    # Remove duplicate elements while maintaining order
    unique_search_paths = []
    seen = set()
    for item in search_paths:
        if item not in seen:
            unique_search_paths.append(item)
            seen.add(item)
    
    if debug_mode:
        print_and_log(Fore.YELLOW + f'Holy shit its unique_search_paths!')
        for mode, path, ending in unique_search_paths:
            print_and_log(f'{mode}\t{path}\t{ending}')
    
    return unique_search_paths

def search_paths_cleanup(search_paths, remove_gameinfo_path=False, remove_all_source_engine_paths=False, vmt = False):
    modes_to_remove = [
        "platform", 
        "game+mod+mod_write+default_write_path", 
        "game_lv", 
        "game+game_write", 
        "gamebin"
    ]

    search_paths = [sp for sp in search_paths if sp[0] not in modes_to_remove]
    
    if debug_mode:
        print_and_log("Search Paths after deletion by mode:")
        for mode, path, ending in search_paths:
            print_and_log(f'{mode}\t{path}\t{ending}')

    endings_to_remove = ["_textures", "_materials", "_vo_", "_lang_", "_sound", "_english"]
    if vmt: endings_to_remove = ["_vo_", "_lang_", "_sound", "_english"]

    search_paths = [
        sp for sp in search_paths 
        if not any(ending in sp[2] for ending in endings_to_remove)
    ]

    if debug_mode:
        for mode, path, ending in search_paths:
            print_and_log(f'{path}\t\t{ending}')

    if remove_gameinfo_path:
        search_paths = [
            sp for sp in search_paths
            if "|gameinfo_path|" not in sp[1] or "|gameinfo_path|/.." in sp[1]
        ]
    if remove_all_source_engine_paths:
        search_paths = [
            sp for sp in search_paths
            if "|all_source_engine_paths|" not in sp[1] or "|all_source_engine_paths|/.." in sp[1]
        ]

    return search_paths

def update_search_paths(search_paths, game_dir, all_source_engine_paths):
    search_paths = [(mode, path.replace("|gameinfo_path|", game_dir), ending) for mode, path, ending in search_paths]
    search_paths = [(mode, path.replace("|all_source_engine_paths|", all_source_engine_paths), ending) for mode, path, ending in search_paths]
    search_paths = [(mode, path.replace("\\", "/").replace("//", "/"), ending) for mode, path, ending in search_paths]
    if debug_mode:
        print_and_log("Updated Search Paths:")
        for mode, path, ending in search_paths:
            print_and_log(f'{path}\t\t{ending}')
    for mode, path, ending in search_paths:
        if not os.path.exists(path):
            print_and_log(Fore.YELLOW + f'Path from gameinfo.txt does not exist:\n{path}\n')
    
    return search_paths

def find_vpks(gameinfo_path):
    all_source_engine_paths = os.path.abspath(os.path.join(get_script_path(), ".."))
    game_dir = os.path.dirname(gameinfo_path)
    found_vpks = []

    def search_for_vpks(directory):
        for root, _, files in os.walk(directory):
            for file in files:
                if file.endswith("_dir.vpk") and not any(x in file for x in ["_materials_", "_lang_", "_vo_", "_sound_"]):
                    found_vpks.append(os.path.join(root, file))
    search_for_vpks(game_dir)
    search_for_vpks(all_source_engine_paths)

    return found_vpks

def find_mdl_in_paths_from_gameinfo(search_paths, hammer_mdl_path):
    hammer_mdl_path = os.path.normpath(hammer_mdl_path)
    hammer_parts = hammer_mdl_path.split(os.sep)
    
    if debug_mode: print_and_log(f"hammer_parts: {hammer_parts}")
    
    if "models" not in hammer_parts:
        print_and_log(Fore.RED + f"[find_mdl_in_paths_from_gameinfo] ERROR! Path must contain 'models' directory")
        return None
    
    models_index = hammer_parts.index("models")
    hammer_dirs = hammer_parts[models_index:]
    
    mdl_filename = hammer_dirs[-1]
    hammer_dirs = hammer_dirs[:-1]

    def search_for_mdl(base_path, hammer_dirs, mdl_filename):
        for root, dirs, files in os.walk(base_path):
            rel_path = os.path.relpath(root, base_path)
            rel_parts = rel_path.split(os.sep)

            if rel_parts[-len(hammer_dirs):] == hammer_dirs:
                if mdl_filename in files:
                    founded_mdl = os.path.join(root, mdl_filename)
                    if debug_mode: print_and_log(f"!!!!!!!!!!!!! founded_mdl: {founded_mdl}")
                    return founded_mdl
        return None

    search_paths = [(path, ending) for parts in search_paths if len(parts) == 3 for mode, path, ending in [parts]]

    for path, ending in search_paths:
        if ending == '*':
            mdl_path = search_for_mdl(path, hammer_dirs, mdl_filename)
            if mdl_path:
                return mdl_path
        elif ending == '.':
            models_path = os.path.join(path, "models")
            mdl_path = search_for_mdl(models_path, hammer_dirs, mdl_filename)
            if mdl_path:
                return mdl_path
        elif not ending or ending.isalpha():
            mdl_path = search_for_mdl(path, hammer_dirs, mdl_filename)
            if mdl_path:
                return mdl_path
        elif ending.endswith('.vpk') or ending == '*.vpk':
            continue

    return None

def find_vmt_in_paths_from_gameinfo(search_paths, material_path):
    material_path = os.path.normpath(material_path)
    hammer_parts = material_path.split(os.sep)
    
    if debug_mode: print_and_log(f"hammer_parts: {hammer_parts}")
    
    if "materials" not in hammer_parts:
        print_and_log(Fore.RED + f"[find_vmt_in_paths_from_gameinfo] ERROR! Path must contain 'materials' directory")
        return None
    
    materials_index = hammer_parts.index("materials")
    hammer_dirs = hammer_parts[materials_index:]
    
    vmt_filename = hammer_dirs[-1]
    hammer_dirs = hammer_dirs[:-1]

    def search_for_vmt(base_path, hammer_dirs, vmt_filename):
        for root, dirs, files in os.walk(base_path):
            rel_path = os.path.relpath(root, base_path)
            rel_parts = rel_path.split(os.sep)

            if rel_parts[-len(hammer_dirs):] == hammer_dirs:
                if vmt_filename in files:
                    founded_vmt = os.path.join(root, vmt_filename)
                    print_and_log(f"!!!!! founded_vmt: {founded_vmt}")
                    return founded_vmt
        return None

    search_paths = [(path, ending) for parts in search_paths if len(parts) == 3 for mode, path, ending in [parts]]

    for path, ending in search_paths:
        if ending == '*':
            vmt_path = search_for_vmt(path, hammer_dirs, vmt_filename)
            if vmt_path:
                return vmt_path
        elif ending == '.':
            materials_path = os.path.join(path, "materials")
            vmt_path = search_for_vmt(materials_path, hammer_dirs, vmt_filename)
            if vmt_path:
                return vmt_path
        elif not ending or ending.isalpha():
            vmt_path = search_for_vmt(path, hammer_dirs, vmt_filename)
            if vmt_path:
                return vmt_path
        elif ending.endswith('.vpk') or ending == '*.vpk':
            continue

    return None

def only_vpk_paths_from_gameinfo(search_paths, vmt = False):
    vpk_files = []
    search_paths = [(path, ending) for parts in search_paths if len(parts) == 3 for mode, path, ending in [parts]]
    
    exclude_list = ["_textures", "_materials", "_lang_", "_vo_", "_sound"]
    if vmt: exclude_list = ["_lang_", "_vo_", "_sound"]
    
    def search_for_vpk(base_path, vpk_files):
        for root, dirs, files in os.walk(base_path):
            for file in files:
                if file.endswith("_dir.vpk") and all(sub not in file for sub in exclude_list):
                    vpk_files.append(os.path.join(root, file))
    for path, ending in search_paths:
        if ending == '*':
            search_for_vpk(path, vpk_files)
        elif ending == '.':
            for file in os.listdir(path):
                if file.endswith("_dir.vpk") and all(sub not in file for sub in exclude_list):
                    vpk_files.append(os.path.join(path, file))
        elif not ending or ending.isalpha():
            search_for_vpk(path, vpk_files)
        elif ending.endswith('.vpk'):
            if ending.endswith(".vpk") and all(sub not in ending for sub in exclude_list):
                if not "_dir.vpk" in ending:
                    vpk_files.append(os.path.join(path, ending.replace(".vpk", "_dir.vpk")))
                else:
                    vpk_files.append(os.path.join(path, ending))
        elif ending == '*.vpk':
            search_for_vpk(path, vpk_files)

    existing_vpk_files = []
    for vpk_file in vpk_files:
        if os.path.exists(vpk_file):
            existing_vpk_files.append(vpk_file)
        else:
            print_and_log(Fore.YELLOW + f'VPK file from gameinfo.txt does not exist:\n{vpk_file}\n')

    return existing_vpk_files

def delete_temp_vpks_content_folder():
    vpk_extract_folder = os.path.join(get_script_path(), extracted_vpks_folder_name)
    if os.path.exists(vpk_extract_folder):
        try:
            shutil.rmtree(vpk_extract_folder)
            print_and_log(f" ")
            print_and_log(f"Folder with temp extracted VPKs content deleted:\n{vpk_extract_folder}")
        except Exception as e:
            print_and_log(Fore.RED + f"ERROR! Folder with extracted vpks content ({vpk_extract_folder}) cant be deleted: {e}")
    else:
            if debug_mode: print_and_log(f"{vpk_extract_folder}' does not exist.")

def entities_todo_processor(entities_raw, entities_ready, entities_todo, psr_cache_data_raw, psr_cache_data_ready, psr_cache_data_todo, ccld_path, gameinfo_path, compiler_path, game_dir, convert_to_static, subfolders, vpkeditcli_path):
    #vpk_extract_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mdl_scaler_vpk_extract")
    vpk_extract_folder = os.path.join(get_script_path(), extracted_vpks_folder_name)

    print_and_log(f" ")
    print_and_log(f"Extracting paths from gameinfo.txt...")
    
    all_source_engine_paths = os.path.abspath(os.path.join(get_script_path(), ".."))
    search_paths = parse_search_paths(gameinfo_path)
    search_paths = search_paths_cleanup(search_paths, remove_gameinfo_path=False, remove_all_source_engine_paths=False)
    search_paths = update_search_paths(search_paths, game_dir, all_source_engine_paths)
    global g_search_paths
    g_search_paths = search_paths
    
    vpk_paths_from_gameinfo = only_vpk_paths_from_gameinfo(search_paths)
    if debug_mode: print_and_log(f"vpk_paths_from_gameinfo: \n{vpk_paths_from_gameinfo}")

    print_and_log(f" ")
    print_and_log(f"Searching for models real paths...")
    #real_mdl_paths_len = len(psr_cache_data_todo.keys())
    #real_mdl_paths_progress = 0
    real_mdl_paths = []
    for hammer_mdl_path in psr_cache_data_todo.keys():
        if debug_mode: print_and_log(f"hammer_mdl_path: {hammer_mdl_path}")
        
        mdl_name = get_file_name(hammer_mdl_path)
        scales_list = psr_cache_data_todo[hammer_mdl_path].get('scales', [])
        #print_and_log(f"scales_list: {scales_list}")
        scales = " ".join(scales_list)  # Преобразуем список scales в строку
        
        # Проверяем наличие real_mdl_path в кэше
        if hammer_mdl_path in psr_cache_data_ready:
            # вот тут берём рендерколоры и скины
            real_mdl_path = psr_cache_data_ready[hammer_mdl_path].get('real_mdl_path', None)
            rendercolor = psr_cache_data_ready[hammer_mdl_path].get('rendercolor', None)
            skin = psr_cache_data_ready[hammer_mdl_path].get('skin', None)
            
            print_and_log(f"real_mdl_path: {real_mdl_path}")
            print_and_log(f"rendercolor: {rendercolor}")
            print_and_log(f"skin: {skin}")
            
            input("zcvbbbbe")
            
            if real_mdl_path is not None:
                print_and_log(Fore.GREEN + f"{mdl_name}.mdl found in cache!")
                
                decompile_rescale_and_compile_model(ccld_path, gameinfo_path, compiler_path, real_mdl_path, scales, convert_to_static, subfolders, hammer_mdl_path, psr_cache_data_todo, psr_cache_data_ready)
                continue

        real_mdl_path = find_real_mdl_path(game_dir, hammer_mdl_path)
        if real_mdl_path:
            #real_mdl_paths.append(real_mdl_path)
            is_static = psr_cache_data_ready.get(hammer_mdl_path, {}).get("is_static", None)
            psr_cache_data_todo = add_to_cache(psr_cache_data_todo, hammer_mdl_path, modelscale="1.0", rendercolor="255 255 255", skin="0", real_mdl_path=real_mdl_path, is_static=is_static)
            psr_cache_data_ready = add_to_cache(psr_cache_data_ready, hammer_mdl_path, modelscale="1.0", rendercolor="255 255 255", skin="0", real_mdl_path=real_mdl_path, is_static=is_static)
            
            decompile_rescale_and_compile_model(ccld_path, gameinfo_path, compiler_path, real_mdl_path, scales, convert_to_static, subfolders, hammer_mdl_path, psr_cache_data_todo, psr_cache_data_ready)
            continue

        else:
            print_and_log(f" ")
            print_and_log(f"{mdl_name}.mdl not found in project content, trying to find in paths from GameInfo...")

            mdl_path_from_other_contents = find_mdl_in_paths_from_gameinfo(search_paths, hammer_mdl_path)
            
            if mdl_path_from_other_contents != None:
                is_static = psr_cache_data_ready.get(hammer_mdl_path, {}).get("is_static", None)
                psr_cache_data_todo = add_to_cache(psr_cache_data_todo, hammer_mdl_path, modelscale="1.0", rendercolor="255 255 255", skin="0", real_mdl_path=mdl_path_from_other_contents, is_static=is_static)
                psr_cache_data_ready = add_to_cache(psr_cache_data_ready, hammer_mdl_path, modelscale="1.0", rendercolor="255 255 255", skin="0", real_mdl_path=mdl_path_from_other_contents, is_static=is_static)
                print_and_log(Fore.GREEN + f"{mdl_name}.mdl found!")
                
                decompile_rescale_and_compile_model(ccld_path, gameinfo_path, compiler_path, mdl_path_from_other_contents, scales, convert_to_static, subfolders, hammer_mdl_path, psr_cache_data_todo, psr_cache_data_ready)
                continue
            else:
                if debug_mode: print_and_log(f"{mdl_name}.mdl not found in paths from gameinfo.txt")
                print_and_log(f"Trying to find {mdl_name}.mdl in vpks...")

                extracted_mdl_path = extract_mdl(vpkeditcli_path, hammer_mdl_path, vpk_extract_folder, vpk_paths_from_gameinfo)
                if debug_mode: print_and_log(Fore.YELLOW + f"extracted_mdl_path: {extracted_mdl_path}")

                if extracted_mdl_path != None:
                    #real_mdl_paths.append(extracted_mdl_path)
                    psr_cache_data_todo = add_to_cache(psr_cache_data_todo, hammer_mdl_path, modelscale="1.0", rendercolor="255 255 255", skin="0", real_mdl_path=extracted_mdl_path)
                    #psr_cache_data_ready = add_to_cache(psr_cache_data_ready, hammer_mdl_path, modelscale="1.0", rendercolor="255 255 255", skin="0", real_mdl_path=extracted_mdl_path)
                    print_and_log(Fore.GREEN + f"{mdl_name}.mdl found!")
                    
                    decompile_rescale_and_compile_model(ccld_path, gameinfo_path, compiler_path, extracted_mdl_path, scales, convert_to_static, subfolders, hammer_mdl_path, psr_cache_data_todo, psr_cache_data_ready)
                    continue
                else:
                    print_and_log(Fore.RED + f"Can't extract {mdl_name}.mdl from VPKs, skipping")

    psr_cache_data_ready_load = load_global_cache()
    if psr_cache_data_ready_load != None: psr_cache_data_ready = psr_cache_data_ready_load

    delete_temp_vpks_content_folder()
    
    return entities_todo, entities_ready

def convert_vmf(game_dir, vmf_in_path, vmf_out_path, subfolders, entities_ready, psr_cache_data_ready):
    #print_and_log(f"convert_vmf start...")
    print_and_log(f"vmf_in_path: {vmf_in_path}")
    print_and_log(f"vmf_out_path: {vmf_out_path}")

    out_dir = os.path.dirname(vmf_out_path)
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    shutil.copy2(vmf_in_path, vmf_out_path)

    with open(vmf_out_path, 'r') as file:
        content = file.read()

    entities_ready_scaled = []
    #entities_ready_scaled_len = len(entities_ready)
    #entities_ready_scaled_progress = 0
    for entity in entities_ready:
        model = entity['model']
        modelscale = entity['modelscale']
        base_name, ext = os.path.splitext(model)
        
        if float(modelscale) == 1.0:
            new_model = f"{base_name}_static{ext}"
        else:
            new_model = f"{base_name}_scaled_{int(float(modelscale) * 100)}{ext}"
        
        if subfolders == True and float(modelscale) != 1.0:
            new_model = os.path.join(os.path.dirname(new_model), 'scaled', os.path.basename(new_model)).replace('\\', '/')

        entity['model'] = new_model

        entities_ready_scaled.append(entity)

    entities_ready_scaled_len = len(entities_ready_scaled)
    print_and_log(f"{entities_ready_scaled_len} entities to insert into the VMF.")

    entities_progress = 0
    
    for entity in entities_ready_scaled:
        if debug_mode: print_and_log(Fore.YELLOW + f"inserting to vmf: {entity}")
        entity_id = entity['id']
        new_model = entity['model']
        modelscale = entity['modelscale']
        
        if debug_mode: print_and_log(Fore.YELLOW + f"new_model: {new_model}")
        if debug_mode: print_and_log(Fore.YELLOW + f"modelscale: {modelscale}")
        
        if float(modelscale) == 1.0:
            #psr_cache_data_ready = add_to_cache(psr_cache_data_ready, hammer_mdl_path, modelscale="1.0", rendercolor="255 255 255", skin="0", is_static=True)
            #save_global_cache(psr_cache_data_ready)
            
            # Проверяем наличие real_mdl_path в кэше
            hammer_mdl_path = new_model.replace('_static', '')
            if hammer_mdl_path in psr_cache_data_ready:
                is_static = psr_cache_data_ready[hammer_mdl_path].get('is_static', None)
                #print_and_log(f"hammer_mdl_path: {hammer_mdl_path}")
                #print_and_log(f"is_static: {is_static}")
                if is_static:
                    #эта ветка срабатывает если оригинальная модель была статичная и надо использовать имя без _static
                    new_model = hammer_mdl_path
                    #new_model = new_model.replace('_static', '')
                    #print_and_log(Fore.GREEN + f"{mdl_name}.mdl found in cache!")
            else:
                mdl_name = get_file_name(new_model)
                real_mdl_path = find_file_in_subfolders(game_dir, f"{mdl_name}.mdl") #вот эта функция жрёт больше всего во всём инсёрте
                if real_mdl_path:
                    # эта ветка срабатывает если модель была динамическая и стала статическая с постфиком _static
                    pass
                else:
                    #если в имени нет _static значит либо оригинальная модель статичная либо компиляция сдохла, предполагаем первое
                    new_model = new_model.replace('_static', '')
        
        if debug_mode: print_and_log(Fore.YELLOW + f"new_model: {new_model}")

        pattern = re.compile(
            r'entity\s*\{\s*"id"\s*"' + re.escape(entity_id) + r'"\s*("classname"\s*"prop_static_scalable"\s*)(".*?"\s*)*?("model"\s*".*?"\s*)(".*?"\s*)*\}', re.DOTALL
        )

        def replacer(match):
            updated_block = match.group(0)
            updated_block = re.sub(r'"classname"\s*"prop_static_scalable"', '"classname" "prop_static"', updated_block)
            updated_block = re.sub(r'"model"\s*".*?"', f'"model" "{new_model}"', updated_block)
            return updated_block

        content = pattern.sub(replacer, content)
        
        entities_progress += 1
        
        if entities_progress >= entities_ready_scaled_len:
            print_and_log(f"Progress: Done!")
        else:
            print(f"Progress: {int(entities_progress*100/entities_ready_scaled_len)}%", end="\r")
    
    with open(vmf_out_path, 'w') as file:
        if debug_mode: print_and_log(Fore.YELLOW + f"writing vmf...")
        file.write(content)

def lightsrad_updater(game_dir, entities_ready):
    lights_rad_path = os.path.join(game_dir, 'lights.rad')
    if not os.path.exists(lights_rad_path):
        print_and_log(f" ")
        print_and_log(f"lights.rad file not found")
        return

    backup_path = os.path.join(game_dir, 'lights.rad_backup')
    shutil.copyfile(lights_rad_path, backup_path)

    with open(lights_rad_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Check for the line “// scaled props list generated by props_scaling_recompiler”.
    header_line = "// forcetextureshadow scaled props list, generated by props_scaling_recompiler\n"
    if header_line not in lines:
        lines.append("\n")
        lines.append(header_line)  # Add a line to the end if there is none

    entities_scaled = []
    for entity in entities_ready:
        model = entity['model']
        if '_scaled_' in model:
            entities_scaled.append(entity)
    
    # Checking that entities_scaled is not empty
    if not entities_scaled:
        print_and_log(f"No scaled models were found to add to lights.rad")
        return

    for entity in entities_scaled:
        model = entity['model']
        model_noroot = '/'.join(model.split('/')[1:])
        model_noroot_original_subf = re.sub(r'_scaled_\d+', '', model_noroot)
        model_noroot_original = model_noroot_original_subf.replace('/scaled/', '/')
        
        if debug_mode: print_and_log(Fore.YELLOW + f"model_noroot: \t\t\t{model_noroot}")
        if debug_mode: print_and_log(Fore.YELLOW + f"model_noroot_original_subf: \t{model_noroot_original_subf}")
        if debug_mode: print_and_log(Fore.YELLOW + f"model_noroot_original: \t\t{model_noroot_original}")

        # Check if the forcetextureshadow string is present for the original model
        found_original_line = any(f"forcetextureshadow {model_noroot_original}" in line for line in lines)
        
        if debug_mode: print_and_log(Fore.YELLOW + f"found_original_line: {found_original_line}")
        
        if found_original_line:
            # If the line is present for the original, check if it is present for the scaled model
            found_scaled_line = any(f"forcetextureshadow {model_noroot}" in line for line in lines)
            
            if debug_mode: print_and_log(Fore.YELLOW + f"[A] found_scaled_line: {found_scaled_line}")
            
            if not found_scaled_line:
                # Add a line for the scaled model
                lines.append(f"forcetextureshadow {model_noroot}\n")
        else:
            # If there is no string for the original model
            model_base = model_noroot.split('_scaled_')[0]
            scaled_pattern = rf"forcetextureshadow {re.escape(model_base)}_scaled_\d+\.mdl"
            
            found_scaled_lines = [line for line in lines if re.search(scaled_pattern, line)]
            
            if debug_mode: print_and_log(Fore.YELLOW + f"[B] found_scaled_lines: {found_scaled_lines}")
            
            if found_scaled_lines:
                # Delete the line for the scaled model
                lines = [line for line in lines if not re.search(scaled_pattern, line)]

    # Сохранение изменений в lights.rad
    with open(lights_rad_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)

    print_and_log(f" ")
    print_and_log(f"lights.rad updated successfully.")

def main():
    # init colorama
    init()
    
    #Fore.BLACK
    #Fore.RED
    #Fore.GREEN
    #Fore.YELLOW
    #Fore.BLUE
    #Fore.MAGENTA
    #Fore.CYAN
    #Fore.WHITE
    #Fore.RESET
    
    # DESCRIPTION
    psr_description_name = f"props_scaling_recompiler 1.1.0"
    psr_description_author = f"Shitcoded by Ambiabstract (Sergey Shavin)"
    psr_description_github = f"https://github.com/Ambiabstract"
    psr_description_discord = f"Discord: @Ambiabstract"
    
    print_and_log(Fore.CYAN + f'{psr_description_name}')
    print_and_log(f'{psr_description_author}')
    print_and_log(f'{psr_description_github}')
    print_and_log(f'{psr_description_discord}')

    start_time = time.time()
    
    script_path = get_script_path()
    
    if debug_mode == True:
        print_and_log(f'script_path: {script_path}\n')
    
    if check_bin_folder(script_path) == True:
        pass
    else:
        print(f" ")
        input("Press Enter to exit...")
        return
    
    if find_file(script_path, filename_ext = "CrowbarCommandLineDecomp.exe") == True:
        pass
    else:
        print_and_log(Fore.RED + "ERROR! This tool requires CrowbarCommandLineDecomp.exe lying in the same bin folder! Please download the program from the author's GitHub and place it there:")
        print_and_log(ccld_url)
        input("\nPress Enter to exit...")
        return
    
    if find_file(script_path, filename_ext = "vpkeditcli.exe") == True:
        vpkeditcli_path = os.path.join(script_path, "vpkeditcli.exe")
        if debug_mode: print_and_log(f"vpkeditcli_path: {vpkeditcli_path}")
    else:
        print_and_log(Fore.RED + "ERROR! This tool requires standalone vpkeditcli.exe lying in the same bin folder! Please download the program from the author's GitHub and place it there:")
        print_and_log(vpkedit_url)
        input("\nPress Enter to exit...")
        return
    
    # delete temporary shit if it didn't deleted last time on error
    delete_temp_vpks_content_folder()
    
    parser = argparse.ArgumentParser(description=f"props_scaling_recompiler usage:")
    
    parser.add_argument('-game', type=str, required=True, help='Path to the game directory')
    parser.add_argument('-vmf_in', type=str, required=True, help='Path to the input .vmf file')
    parser.add_argument('-vmf_out', type=str, required=True, help='Path to the output .vmf file')
    parser.add_argument('-subfolders', type=int, required=False, default=1, help='Using subfolders (0 or 1)')
    parser.add_argument('-force_recompile', type=int, required=False, default=0, help='Recompile all props for this map (0 or 1)')

    try:
        args = parser.parse_args()
    except SystemExit as e:
        os.system('cls' if os.name == 'nt' else 'clear')
        print_and_log(Fore.CYAN + f'{psr_description_name}')
        print_and_log(f'{psr_description_author}')
        print_and_log(f'{psr_description_github}')
        print_and_log(f'{psr_description_discord}')
        print_and_log(f' ')
        print_and_log(Fore.RED + f"ERROR! Input args not found!")
        if e.code != 0:  # if the exit code is not 0, it means there was an error in parsing arguments
            parser.print_help()
        print_and_log(f' ')
        input("Press Enter to exit...")
        sys.exit(e.code)

    game_dir = args.game
    gameinfo_path = os.path.join(game_dir, "GameInfo.txt")
    vmf_in_path = args.vmf_in
    vmf_out_path = args.vmf_out
    
    if debug_mode: print_and_log("Game directory:", args.game)
    if debug_mode: print_and_log("Input VMF file:", args.vmf_in)
    if debug_mode: print_and_log("Output VMF file:", args.vmf_out)
    if debug_mode: print_and_log("Subfolders flag:", args.subfolders)
    if debug_mode: print_and_log("Force recompile:", args.force_recompile)
    
    if args.subfolders == 1:
        subfolders = True
    else:
        subfolders = False
    
    if args.force_recompile == 1:
        force_recompile = True
    else:
        force_recompile = False

    ccld_path = os.path.join(script_path, "CrowbarCommandLineDecomp.exe")
    compiler_path = os.path.join(script_path, "studiomdl.exe")
    convert_to_static = False

    psr_cache_data_ready = {}
    psr_cache_data_ready_load = load_global_cache()
    print_and_log(f" ")
    #print_and_log(f"psr_cache_data_ready_load: {psr_cache_data_ready_load}")
    if psr_cache_data_ready_load != None: 
        psr_cache_data_ready = psr_cache_data_ready_load
        print_and_log(f"Cache loaded: props_scaling_recompiler_cache.pkl")
    else:
        print_and_log(f"Cache not found.")
    
    #print_and_log(f" ")
    #print_and_log(f"GLOBAL CACHE ON THE START:")
    #print_and_log(f"{psr_cache_data_ready}")

    entities_raw, entities_ready, entities_todo, psr_cache_data_raw, psr_cache_data_ready, psr_cache_data_todo = process_vmf(game_dir, vmf_in_path, psr_cache_data_ready, force_recompile, classnames = ["prop_static_scalable"])

    if len(entities_raw) == 0:
        print_and_log(f"Copying VMF...")
        print_and_log(f"vmf_in_path: {vmf_in_path}")
        print_and_log(f"vmf_out_path: {vmf_out_path}")
    
        out_dir = os.path.dirname(vmf_out_path)
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)
    
        shutil.copy2(vmf_in_path, vmf_out_path)
        print_and_log(f"Done.")
        return

    if len(entities_todo) != 0:
        print_and_log(f" ")
        print_and_log(f"There's something to do...")
        entities_todo, entities_ready = entities_todo_processor(entities_raw, entities_ready, entities_todo, psr_cache_data_raw, psr_cache_data_ready, psr_cache_data_todo, ccld_path, gameinfo_path, compiler_path, game_dir, convert_to_static, subfolders, vpkeditcli_path)
    else:
        print_and_log(Fore.GREEN + f"Nothing to recompile!")

    if debug_mode: print_and_log(f"\n entities_ready: {entities_ready}")
    
    psr_cache_data_ready_load = load_global_cache()
    if psr_cache_data_ready_load != None: psr_cache_data_ready = psr_cache_data_ready_load
    
    #print_and_log(f" ")
    #print_and_log(f"entities_ready: {entities_ready}")
    #print_and_log(f" ")
    #print_and_log(f"psr_cache_data_ready: {psr_cache_data_ready}")
    #print_and_log(f" ")
    #print_and_log(f"len(entities_raw): {len(entities_raw)}")
    #print_and_log(f" ")
    #print_and_log(f"entities_raw: {entities_raw}")
    #print_and_log(f" ")
    #print_and_log(f"len(entities_ready): {len(entities_ready)}")
    #print_and_log(f" ")
    #print_and_log(f"entities_ready: {entities_ready}")
    #print_and_log(f" ")
    #print_and_log(f"psr_cache_data_raw: {psr_cache_data_raw}")

    #lightsrad_updater(game_dir, entities_ready)
    
    # entities_ready = entities_raw just because
    
    print_and_log(f" ")
    print_and_log(f"Processing output VMF, please wait...")
    convert_vmf(game_dir, vmf_in_path, vmf_out_path, subfolders, entities_raw, psr_cache_data_ready)
    
    print_and_log(f" ")
    end_time = time.time()
    elapsed_time = end_time - start_time
    hours, remainder = divmod(elapsed_time, 3600)
    minutes, seconds = divmod(remainder, 60)
    print_and_log(f"Time spent: {int(hours)} hours, {int(minutes)} minutes, {seconds:.2f} seconds")
    
    print_and_log(Fore.GREEN + f"props_scaling_recompiler has finished its work!")
    print_and_log(f" ")
    
    # Closing colorama
    #deinit()

try:
    if __name__ == '__main__':
        main()
except Exception as e:
    import traceback
    print_and_log(Fore.RED + f"An error occurred: {e}")
    print_and_log(traceback.format_exc())
    input("\nPress Enter to exit...")
finally:
    with open(f"{get_script_name()}_log.txt", 'w', encoding='utf-8') as f:
        f.write(log_buffer.getvalue())
    #input("\nPress Enter to exit...")
    pass