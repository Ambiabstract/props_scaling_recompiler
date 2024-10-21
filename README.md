# props_scaling_recompiler

## Description:
props_scaling_recompiler is an automatic assets recompiler that allows you to scale props and/or convert them to prop_static without leaving [Hammer++](https://ficool2.github.io/HammerPlusPlus-Website/).

If you work with Source SDK 2013 and often use prop_scalable, this tool can be particularly useful. prop_scalable is a dynamic entity, which means it consumes entdata, does not cast lightmap shadows, lacks baked vertex lighting, and its collision does not scale with its visual geometry.

![aa_models_test_01a0010](https://github.com/user-attachments/assets/1ae07220-df59-400a-8475-413da770286a)
![изображение](https://github.com/user-attachments/assets/c3459319-84e6-4f55-b88a-2a706f1a0338)

The main reference is the "uniformscale" feature of prop_static from the CS:GO SDK.

The primary purpose of this tool is to save level designers from using prop_scalable or manually recompiling models.

Only [Hammer++](https://ficool2.github.io/HammerPlusPlus-Website/) is supported. Functionality with other editors is not guaranteed.


## Working together with Propper++
	
[Propper++](https://developer.valvesoftware.com/wiki/Hammer++/Propper++) includes a function for static prop scaling, capable of scaling assets on separate axes. It can also merge models into one prop_static like Static Prop Combine from the CS:GO SDK, among other features.

However, Propper++ has three key disadvantages for our purposes: no asset preview before creation, manual path entry, and no conversion to static prop.

Instead of a separate menu, we use an approach involving entities on the map, which are easy to place and modify. The preview of the prop is available to the user immediately, while scaled/converted assets appear only during the map compilation process and are automatically saved near the original models, simplifying future asset management.

In summary, these tools complement each other rather than exclude each other.

![ezgif-3-9343f7bb04](https://github.com/user-attachments/assets/7176845e-8e00-4365-b0fa-dba6f7481687)

By the way, with the "Shift + Mouse Wheel" key combination you can scale selected prop without opening any menus at all!

![GIF 21 10 2024 4-43-34](https://github.com/user-attachments/assets/330ce557-45ee-4201-8e96-22700b35352d)


## Small content size:
Since the tool creates scaled copies of models, you should consider the impact on the overall size of new files in the mod content.
	
However, props_scaling_recompiler creates copies only of the geometry and does not create copies of materials/textures, so a significant increase in the size of the project's files is not expected.
	
Currently, the ratio of copies to megabytes is approximately 4 to 1, i.e., 4 new models will weigh about 1 MB (including LODs and collisions). 

This means that with an additional 100 MB of project content size, you can afford approximately 400 scaled versions of assets (maybe less, maybe more - it depends on the asset itself).

After packing this content into VPK, the size will be even smaller.


## Installation:
Video tutorial (English subtitles available):

[![Click to open Youtube video](https://img.youtube.com/vi/3PpmJrCmTKQ/0.jpg)](https://www.youtube.com/watch?v=3PpmJrCmTKQ)

1. [**Download zip archive with all stuff**](https://github.com/Ambiabstract/props_scaling_recompiler/releases/latest)

2. Put props_scaling_recompiler.exe, [CrowbarCommandLineDecomp.exe](https://github.com/UltraTechX/Crowbar-Command-Line), [vpkeditcli.exe](https://github.com/craftablescience/VPKEdit) and props_scaling_recompiler.fgd in the bin folder where studiomdl.exe is located.

   For example:

   `C:\Program Files (x86)\Steam\steamapps\common\Source SDK Base 2013 Singleplayer\bin\`

3. Open Hammer++. Go to `Tools -> Options -> Game Configuration`, add `props_scaling_recompiler.fgd` to the `Game Data files` list and click "OK".

   Pay attention to the selected project in the Configuration drop-down list: your project should be there! Add this fgd for all projects where you plan to use the tool!

4. Press F9 (Run Map) and go to Expert mode if you are in Normal mode.

   Here you need to change the map compilation settings.

5. Click New to add another step.

   If you are using [VMFii](https://github.com/Metapyziks/VMFInstanceInserter) or any other tool that duplicates your VMF to compile later - put our new item after them. For example if you only use [VMFii](https://github.com/Metapyziks/VMFInstanceInserter), our item should be second in the list.

   ![изображение](https://github.com/user-attachments/assets/5dfb0427-d180-44c5-b118-a78b63c12a46)

   If you don't use [VMFii](https://github.com/Metapyziks/VMFInstanceInserter) and other such tools - drag our item to the very beginning.

   ![изображение](https://github.com/user-attachments/assets/e40e047e-1d55-4522-8404-81ae7120d161)


6. In Command Properties, specify Command. This should be the path to props_scaling_recompiler.exe, for example:

   `C:\Program Files (x86)\Steam\steamapps\common\Source SDK Base 2013 Singleplayer\bin\props_scaling_recompiler.exe`

   The path to this file can be specified not manually, but by clicking `Cmds -> Executable` and then selecting our executable file through explorer.

7. Now it is necessary to specify Parameters. Default parameters will be as follows:

   `-game $gamedir -vmf_in $path\$file.vmf -vmf_out $path\psr_temp\$file.vmf -subfolders 1 -force_recompile 0`

   It is important to pay attention to the following parameters:

   `-vmf_in $path\$file.vmf` - this path should be if you don't use VMFii and other programs that create a copy of vmf to change something in it and compile the copy. If you use VMFii and similar tools, you should specify the path to the vmf that is output by the previous tool. For example, `-vmf_in $path\inst_fix\$file.vmf` if the conditional VMFii outputs its vmf to the `inst_fix` folder.

   `-vmf_out $path\psr_temp\$file.vmf` - this is the vmf that props_scaling_recompiler will output and which should be specified in the following Compile/run commands!

   `-subfolders 1` - put the scaled versions of the props in a separate subfolder (1 = yes, 0 = no)

   `-force_recompile 0` - recompile all scaled props that are available on the level from scratch (1 = yes, 0 = no). For example, this can be useful if the original non-scaled model has been modified.

8. Go through Compile/run commands and specify correct paths in Parameters. It should be the path that props_scaling_recompiler outputs.

## Usage example:
1. Create a new entity.

2. Select its Class `prop_static_scalable`.

3. The usual setting is taking place. In addition to the usual settings for prop_static we now have `Model Scale`. We set the scale value as a multiplier, i.e. 2 will mean a 2-fold increase of the prop (the name of the new asset in the content will have the postfix “_scaled_200”, i.e. 200% of the original size).

   Note: this is true for static and most dynamic props, but not for physics props. For some reason physics props are scaled not by N times, but by N^2 times. I.e. a 2x increase will actually increase the model by a factor of 4, model scale 4 will increase the model by a factor of 16. The scaled model will be put into the project content with the name from the "model scale", not the actual scaled values.

4. Compile the map. After the tool has done its work - a new model with a different scale will appear in the content.

5. If everything has been set up correctly and the compilation was successful, a scaled and static version of the model will be waiting for you in the game.

## Known issues:
1. In some cases dynamic and physics props will have incorrect visualization and/or collision. Some of these problems will be fixed in the future, but not all of them. In some cases modification of the original asset will be required.

2. There may be errors when compiling some dynamic props, for example “models/props_c17/door02_double.mdl”. Whether this will be fixed in the future is still unknown.

## Future plans:

- Auto update of lights.rad (if the original prop is included in this file - its scaled versions will be added automatically).
- Parsing the mdl header to see in advance the presence of the $staticprop parameter (reduce script runtime).
- "_dir" ending support when parsing gameinfo.
- Portal 2 support research.
- New entity - scaling physical props with preserving correct collision and converting any props to physical props.
- New entity - scaling dynamic props with preserving correct collision and converting any props to dynamic props.
- New feature for painting static props, analogous to "rendercolor" from newer versions of the engine.
- Static Prop Combine analog. Merger of several models into one static prop. This functionality is already available in Propper++, but we have some ideas how to improve it.

## Credits:
Thanks to ficool2 for Hammer++ and Propper++

https://ficool2.github.io/HammerPlusPlus-Website/


Thanks to UltraTechX for CrowbarCommandLineDecomp 

https://github.com/UltraTechX/Crowbar-Command-Line


Thanks to craftablescience (Laura Lewis) and contributors for vpkeditcli.exe 

https://github.com/craftablescience/VPKEdit


Thanks Metapyziks for VMFInstanceInserter (VMFii)

https://github.com/Metapyziks/VMFInstanceInserter


Thanks to aptekarr, MyCbEH and v3sp4 for the request, testing and suggestions for improvements.

## Screenshots:
![photo_2024-08-01_03-47-43](https://github.com/user-attachments/assets/419fc2b4-7ff9-4ddc-a987-f63aa626b7b8)
![aa_models_test_01a0012](https://github.com/user-attachments/assets/22eadadc-a277-4bfe-b950-b8d11488875f)
![aa_models_static_convert_test_01a0000](https://github.com/user-attachments/assets/16fee47e-0c88-40c0-af9a-2b0405bc2f0f)
![aa_models_test_01a0023](https://github.com/user-attachments/assets/c268c60e-950d-456a-9e8f-fdabf0e8ab8c)
![aa_models_test_01a0024](https://github.com/user-attachments/assets/f0112d88-b216-41e9-a001-3c1c5c3f4eeb)
![aa_models_static_convert_test_01a0001](https://github.com/user-attachments/assets/71b3443d-d184-45bc-b94c-795fa638877b)
![aa_models_static_convert_test_01a0002](https://github.com/user-attachments/assets/55faec14-3906-4771-9e30-d4b32298437d)
![aa_models_test_01a0025](https://github.com/user-attachments/assets/f4489a3c-4ff5-4dd2-a865-03405a34ebd2)
