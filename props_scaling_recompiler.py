import re
import os
import sys
import subprocess
import shutil
import argparse
import io
import time
from colorama import init, Fore

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

def parse_vmf(file_path, classnames = ["prop_static_scalable", "prop_dynamic_scalable", "prop_physics_scalable"]):
    entities_raw = []
    with open(file_path, 'r') as file:
        content = file.read()

    classnames_pattern = '|'.join(classnames)

    pattern = re.compile(
        rf'entity\s*\{{'
        rf'[^\{{}}]*"id"\s*"(?P<id>\d+)"\s*'
        rf'[^\{{}}]*"classname"\s*\"(?P<classname>{classnames_pattern})\"\s*'
        rf'[^\{{}}]*"model"\s*"(?P<model>[^"]+)"\s*'
        rf'[^\{{}}]*"modelscale"\s*"(?P<modelscale>[^"]+)"\s*'
        rf'[^\{{}}]*"origin"\s*"(?P<origin>[^"]+)"\s*',
        re.DOTALL | re.MULTILINE
    )
    matches = list(pattern.finditer(content))
    
    print_and_log(f" ")
    print_and_log(f"{len(matches)} entities with modelscale found")
    
    for match in matches:
        if debug_mode: print_and_log(f" ")
        
        entity_id = match.group('id')
        if debug_mode: print_and_log(f"id: {entity_id}")
        
        classname = match.group('classname')
        if debug_mode: print_and_log(f"classname: {classname}")
        
        model = match.group('model')
        if debug_mode: print_and_log(f"model: {model}")
        
        modelscale = match.group('modelscale')
        if debug_mode: print_and_log(f"modelscale: {modelscale}")
        if "," in modelscale:
            print_and_log(Fore.YELLOW + f"Warning! Model scale of {get_file_name(model)}.mdl has a comma! Entity ID: {entity_id}. Entity origin: '{origin}'. Compiling with scale 1.")
            modelscale = "1"
        
        origin = match.group('origin')
        if debug_mode: print_and_log(f"origin: {origin}")

        entity_dict = {
            "id": entity_id,
            "model": model,
            "modelscale": modelscale
        }

        entities_raw.append(entity_dict)
    
    print_and_log(f" ")

    return entities_raw

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

def process_entities_raw(game_dir, entities_raw, force_recompile):
    entities_ready = []
    entities_todo = []

    entities_raw_temp = []
    for entity in entities_raw:
        #if float(entity['modelscale']) == 1.0: # This was before we started converting 
        #    entities_ready.append(entity)      # any type of assets to static props.
        if float(entity['modelscale']) >= 0.01:
            entities_raw_temp.append(entity)
        else:
            print_and_log(Fore.RED + f"ERROR! {get_file_name(entity['model'])}.mdl has wrong scale: {entity['modelscale']}. Should be more than 0.01! Skipping")
    
    entities_raw = entities_raw_temp
    
    entities_raw_len = len(entities_raw)
    entities_raw_progress = 0
    
    for entity in entities_raw:
        entity_id = entity['id']
        model = entity['model']
        modelscale = entity['modelscale']

        mdl_name = get_file_name(model)
        mdl_name = process_mdl_name(mdl_name, modelscale)
        mdl_path = find_mdl_file(game_dir, mdl_name)

        if force_recompile:
            entities_todo.append(entity)
        else:
            if mdl_path is None:
                entities_todo.append(entity)
            else:
                entity['model'] = transform_mdl_path_to_hammer_style(mdl_path)
                entity['modelscale'] = '1'
                entities_ready.append(entity)

        entities_raw_progress += 1
        
        if entities_raw_progress >= entities_raw_len:
            print_and_log(f"Progress: Done!")
        else:
            print(f"Progress: {int(entities_raw_progress*100/entities_raw_len)}%", end="\r")

    return entities_ready, entities_todo

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

def compile_model(compiler_path, game_folder, qc_path):
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
        #print_and_log("Errors:", result.stderr.decode())
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

def rescale_qc_file(qc_path, scale, convert_to_static=False, subfolders=True):
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
                return None
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

def copy_and_rescale_qc(qc_path, scale, convert_to_static, subfolders):
    dir_name, file_name = os.path.split(qc_path)
    base_name, ext = os.path.splitext(file_name)
    new_file_name = f"{base_name}_scaled_{int(scale*100)}{ext}"
    new_qc_path = os.path.join(dir_name, new_file_name)
    shutil.copy(qc_path, new_qc_path)
    new_qc_path = rescale_qc_file(new_qc_path, scale, convert_to_static, subfolders)
    return new_qc_path

def rescale_and_compile_models(qc_path, compiler_path, game_folder, scales, convert_to_static, subfolders):    
    scales = list(set(map(float, scales.split())))
    scales.sort()

    for scale in scales:
        new_qc_path = copy_and_rescale_qc(qc_path, scale, convert_to_static, subfolders)
        if new_qc_path != None:
            compile_model(compiler_path, game_folder, new_qc_path)
        else:
            print_and_log(Fore.YELLOW + f"Skip QC compiling (new_qc_path is none for some reason):\n{qc_path}")

def get_valid_path(prompt_message, valid_extension):
    while True:
        path = input(prompt_message).strip().strip('"')
        if os.path.isfile(path) and path.lower().endswith(valid_extension):
            return path
        else:
            print_and_log(Fore.RED + f"File not found, path is incorrect, or file does not have {valid_extension} extension. Try again.")

def decompile_dialog(mdl_path, ccld_path):    
    model_name = os.path.splitext(os.path.basename(mdl_path))[0]
    decomp_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mdl_scaler_decomp")
    #decomp_folder = r"C:\Code\PYTHON\PROP_STATIC_SCALABLE\props_scaling_recompiler_temp\decomp_folder_debug"
    decomp_folder = os.path.join(decomp_folder, model_name)
    if debug_mode: print_and_log(f"decomp_folder: {decomp_folder}")
    
    if os.path.exists(mdl_path):
        if debug_mode: print_and_log(f"mdl_path exist: {mdl_path}")
        if debug_mode: print_and_log(f"running decompilation...")
    else:
        print_and_log(Fore.RED + f"ERROR! mdl_path is not exist: {mdl_path}")
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

def decompile_rescale_and_compile_model(ccld_path, gameinfo_path, compiler_path, mdl_path, scales, convert_to_static, subfolders):
    if debug_mode: print_and_log(f"\ndecompile_rescale_and_compile_model start\n")
    if debug_mode: print_and_log(f"ccld_path: {ccld_path}")
    if debug_mode: print_and_log(f"gameinfo_path: {gameinfo_path}")
    if debug_mode: print_and_log(f"compiler_path: {compiler_path}")
    if debug_mode: print_and_log(f"mdl_path: {mdl_path}")
    if debug_mode: print_and_log(f"scales: {scales}")
    qc_path = decompile_dialog(mdl_path, ccld_path)
    if debug_mode: print_and_log(f"qc_path: {qc_path}")
    game_folder = gameinfo_path.rsplit('\\', 1)[0]
    if debug_mode: print_and_log(f"game_folder: {game_folder}")
    rescale_and_compile_models(qc_path, compiler_path, game_folder, scales, convert_to_static, subfolders)

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
    
    
    #print_and_log(f"mdl_folder_path_orig: {mdl_folder_path_orig}")
    #print_and_log(f"mdl_name_with_ext: {mdl_name_with_ext}")
    
    #print_and_log(f"mdl_parent_folder_name: {mdl_parent_folder_name}")
    
    #mdl_with_parent_folder = mdl_parent_folder_name + "/" + mdl_name_with_ext
    #print_and_log(f"mdl_with_parent_folder: {mdl_with_parent_folder}")

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
                #дополнительная проверка
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

                #print_and_log(f"vpk_file: {vpk_file}")
                #print_and_log(f"mdl_folder_path_orig: {mdl_folder_path_orig}")
                #print_and_log(f"mdl_name_with_ext: {mdl_name_with_ext}")
                #print_and_log(f"folder_check: {folder_check}")
                #print_and_log(f"model_check: {model_check}")
                
                #if folder_check and model_check:
                #    vpk_with_mdl = vpk_file
                
                #input("zxcv")
                
                #print_and_log(f"vpkeditcli_tree_out: {vpkeditcli_tree_out}")
                #print_and_log(f"mdl_folder_path_orig: {mdl_folder_path_orig}")
                #print_and_log(f"mdl_name_with_ext: {mdl_name_with_ext}")
                
                #if debug_mode: print_and_log(f"vpkeditcli_tree_out: {vpkeditcli_tree_out}")
                
                #print_and_log(f"hammer_mdl_path_no_first_folder: {hammer_mdl_path_no_first_folder}")
                #print_and_log(f"mdl_name_with_ext: {mdl_name_with_ext}")
                #print_and_log(f"mdl_folder_path_orig: {mdl_folder_path_orig}")

                if debug_mode: print_and_log(f"mdl_name_with_ext: {mdl_name_with_ext}")
                if debug_mode: print_and_log(f"os.path.dirname(hammer_mdl_path): {os.path.dirname(hammer_mdl_path)}")
                if debug_mode: print_and_log(f"hammer_mdl_path: {hammer_mdl_path}")
                if debug_mode: print_and_log(f"mdl_folder_path: {mdl_folder_path}")
                if debug_mode: print_and_log(f"vpk_extract_folder_model_with_last_folder: {vpk_extract_folder_model_with_last_folder}")
        
            if vpk_with_mdl != None:
                break
        
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

def search_paths_cleanup(search_paths, remove_gameinfo_path=False, remove_all_source_engine_paths=False):
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

def only_vpk_paths_from_gameinfo(search_paths):
    vpk_files = []
    search_paths = [(path, ending) for parts in search_paths if len(parts) == 3 for mode, path, ending in [parts]]
    def search_for_vpk(base_path, vpk_files):
        for root, dirs, files in os.walk(base_path):
            for file in files:
                if file.endswith("_dir.vpk") and all(sub not in file for sub in ["_textures", "_materials", "_lang_", "_vo_", "_sound"]):
                    vpk_files.append(os.path.join(root, file))
    for path, ending in search_paths:
        if ending == '*':
            search_for_vpk(path, vpk_files)
        elif ending == '.':
            for file in os.listdir(path):
                if file.endswith("_dir.vpk") and all(sub not in file for sub in ["_textures", "_materials", "_lang_", "_vo_", "_sound"]):
                    vpk_files.append(os.path.join(path, file))
        elif not ending or ending.isalpha():
            search_for_vpk(path, vpk_files)
        elif ending.endswith('.vpk'):
            if ending.endswith(".vpk") and all(sub not in ending for sub in ["_textures", "_materials", "_lang_", "_vo_", "_sound"]):
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

def entities_todo_processor(entities_todo, entities_ready, ccld_path, gameinfo_path, compiler_path, game_dir, convert_to_static, subfolders, vpkeditcli_path):
    #vpk_extract_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mdl_scaler_vpk_extract")
    vpk_extract_folder = os.path.join(get_script_path(), extracted_vpks_folder_name)

    mdl_with_scales = {}
    for entity in entities_todo:
        model = entity['model']
        modelscale = entity['modelscale']
        mdl_name = model
        
        if mdl_name not in mdl_with_scales:
            mdl_with_scales[mdl_name] = set()
        mdl_with_scales[mdl_name].add(modelscale)

    print_and_log(f" ")
    print_and_log(f"Extracting paths from gameinfo.txt...")
    
    all_source_engine_paths = os.path.abspath(os.path.join(get_script_path(), ".."))
    search_paths = parse_search_paths(gameinfo_path)
    search_paths = search_paths_cleanup(search_paths, remove_gameinfo_path=False, remove_all_source_engine_paths=False)
    search_paths = update_search_paths(search_paths, game_dir, all_source_engine_paths)
    
    vpk_paths_from_gameinfo = only_vpk_paths_from_gameinfo(search_paths)
    if debug_mode: print_and_log(f"vpk_paths_from_gameinfo: \n{vpk_paths_from_gameinfo}")

    real_mdl_paths = []
    for mdl_name in mdl_with_scales.keys():
        hammer_mdl_path = mdl_name
        if debug_mode: print_and_log(f"hammer_mdl_path: {hammer_mdl_path}")
        mdl_name = get_file_name(hammer_mdl_path)
        real_mdl_path = find_real_mdl_path(game_dir, hammer_mdl_path)
        if real_mdl_path:
            real_mdl_paths.append(real_mdl_path)
        else:
            print_and_log(f" ")
            print_and_log(f"{mdl_name}.mdl not found in project content, trying to find in paths from GameInfo...")

            mdl_path_from_other_contents = find_mdl_in_paths_from_gameinfo(search_paths, hammer_mdl_path)
            
            if mdl_path_from_other_contents != None:
                real_mdl_paths.append(mdl_path_from_other_contents)
                print_and_log(Fore.GREEN + f"{mdl_name}.mdl found!")
            else:
                if debug_mode: print_and_log(f"{mdl_name}.mdl not found in paths from gameinfo.txt")
                print_and_log(f"Trying to find {mdl_name}.mdl in vpks...")

                extracted_mdl_path = extract_mdl(vpkeditcli_path, hammer_mdl_path, vpk_extract_folder, vpk_paths_from_gameinfo)
                if debug_mode: print_and_log(Fore.YELLOW + f"extracted_mdl_path: {extracted_mdl_path}")

                if extracted_mdl_path != None:
                    real_mdl_paths.append(extracted_mdl_path)
                else:
                    print_and_log(Fore.RED + f"Can't extract {mdl_name} from VPKs, skipping")

    for real_mdl_path in real_mdl_paths:
        mdl_file_name = get_file_name(real_mdl_path)
        mdl_name = transform_mdl_path_to_hammer_style(real_mdl_path)
        if mdl_name == None:
            print_and_log(Fore.RED + f"Cant recompile and scale {mdl_file_name}.mdl because of hammer style path transform error, skipping :(")
            continue
        scales = " ".join(mdl_with_scales[mdl_name])
        decompile_rescale_and_compile_model(ccld_path, gameinfo_path, compiler_path, real_mdl_path, scales, convert_to_static, subfolders)

    for entity in entities_todo:
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

        entities_ready.append(entity)

    delete_temp_vpks_content_folder()
    
    return entities_todo, entities_ready

def convert_vmf(vmf_in_path, vmf_out_path, entities_ready, game_dir):
    if debug_mode: print_and_log(f"convert_vmf start...")
    if debug_mode: print_and_log(Fore.YELLOW + f"vmf_in_path: {vmf_in_path}")
    if debug_mode: print_and_log(Fore.YELLOW + f"vmf_out_path: {vmf_out_path}")
    # Create a destination directory if it does not exist
    out_dir = os.path.dirname(vmf_out_path)
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    shutil.copy2(vmf_in_path, vmf_out_path)

    with open(vmf_out_path, 'r') as file:
        content = file.read()

    entities_ready_len = len(entities_ready)
    print_and_log(f"{entities_ready_len} entities to insert.")
    
    entities_progress = 0
    
    for entity in entities_ready:
        if debug_mode: print_and_log(Fore.YELLOW + f"inserting to vmf: {entity}")
        entity_id = entity['id']
        new_model = entity['model']
        modelscale = entity['modelscale']
        
        if debug_mode: print_and_log(Fore.YELLOW + f"new_model: {new_model}")
        if debug_mode: print_and_log(Fore.YELLOW + f"modelscale: {modelscale}")
        
        if float(modelscale) == 1.0:
            mdl_name = get_file_name(new_model)
            real_mdl_path = find_file_in_subfolders(game_dir, f"{mdl_name}.mdl")
            if real_mdl_path:
                pass
            else:
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
        
        if entities_progress >= entities_ready_len:
            print_and_log(f"Progress: Done!")
        else:
            print(f"Progress: {int(entities_progress*100/entities_ready_len)}%", end="\r")
    
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
    print_and_log(Fore.CYAN + f'props_scaling_recompiler 1.0.8')
    print_and_log(f'Shitcoded by Ambiabstract (Sergey Shavin)')
    print_and_log(f'https://github.com/Ambiabstract')
    print_and_log(f'Discord: @Ambiabstract')
    print(Fore.BLACK + f'ANUS SUPER SCALER COMPILER (ASSC) :DDDDDDDDDDDDDDDDDDDDDD xDdxXDXCCCDXXXXDXDXD' + Fore.RESET)

    start_time = time.time()
    
    script_path = get_script_path()
    
    if debug_mode == True:
        print_and_log(f'script_path: {script_path}\n')
    
    if check_bin_folder(script_path) == True:
        pass
    else:
        input("\nPress Enter to exit...")
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
    
    parser = argparse.ArgumentParser(description=f"props_scaling_recompiler")
    
    parser.add_argument('-game', type=str, required=True, help='Path to the game directory')
    parser.add_argument('-vmf_in', type=str, required=True, help='Path to the input .vmf file')
    parser.add_argument('-vmf_out', type=str, required=True, help='Path to the output .vmf file')
    parser.add_argument('-subfolders', type=int, required=False, default=1, help='Using subfolders (0 or 1)')
    parser.add_argument('-force_recompile', type=int, required=False, default=0, help='Recompile all props for this map (0 or 1)')

    try:
        args = parser.parse_args()
    except SystemExit as e:
        os.system('cls' if os.name == 'nt' else 'clear')
        print_and_log(Fore.RED + f"ERROR! Input args not found!")
        if e.code != 0:  # if the exit code is not 0, it means there was an error in parsing arguments
            parser.print_help()
        input("\nPress Enter to exit...")
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
    
    print_and_log(f"Processing initiated")
    
    entities_raw = parse_vmf(vmf_in_path, classnames = ["prop_static_scalable"])
    if debug_mode: print_and_log(f"\nentities_raw: {entities_raw}")
    
    if force_recompile: print_and_log(f"Force recompile mode: scaled and static assets removing from project files...")
    if force_recompile: remove_vmf_assets(entities_raw, game_dir, remove_static=True)
    
    print_and_log(f"Validating prop_static_scalable entities...")
    entities_ready, entities_todo = process_entities_raw(game_dir, entities_raw, force_recompile)
    if debug_mode: print_and_log(f"\nentities_ready: {entities_ready}")
    if debug_mode: print_and_log(f"\nentities_todo: {entities_todo}")

    if len(entities_todo) != 0:
        print_and_log(f" ")
        print_and_log(f"There's something to do...")
        entities_todo, entities_ready = entities_todo_processor(entities_todo, entities_ready, ccld_path, gameinfo_path, compiler_path, game_dir, convert_to_static, subfolders, vpkeditcli_path)
    else:
        print_and_log(Fore.GREEN + f"Nothing to recompile!")

    if debug_mode: print_and_log(f"\n entities_ready: {entities_ready}")
    
    lightsrad_updater(game_dir, entities_ready)
    
    print_and_log(f" ")
    print_and_log(f"Processing VMF...")
    convert_vmf(vmf_in_path, vmf_out_path, entities_ready, game_dir)
    
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