Description:
	
	props_scaling_recompiler is an automatic assets recompiler that allows you to scale props and/or convert them to prop_static without leaving Hammer++.
	
	If you work with Source SDK 2013 and often use prop_scalable, this tool can be particularly useful. prop_scalable is a dynamic entity, which means it consumes entdata, does not cast lightmap shadows, lacks baked vertex lighting, and its collision does not scale with its visual geometry.
	
	The main reference is the uniformscale feature of prop_static from the CS:GO SDK.
	The primary purpose of this tool is to save level designers from using prop_scalable or manually recompiling models.
	
	Only Hammer++ is supported. Functionality with other editors is not guaranteed.


Working together with Propper++
	
	Propper++ includes a function for static prop scaling, capable of scaling assets on separate axes. It can also merge models into one prop_static like Static Prop Combine from the CS:GO SDK, among other features.

	However, Propper++ has three key disadvantages for our purposes: no asset preview before creation, manual path entry, and no conversion to static prop.
	
	Instead of a separate menu, we use an approach involving entities on the map, which are easy to place and modify. The preview of the prop is available to the user immediately, while scaled/converted assets appear only during the map compilation process and are automatically saved near the original models, simplifying future asset management.

	In summary, these tools complement each other rather than exclude each other.


Installation:
	
	1. Put props_scaling_recompiler.exe, CrowbarCommandLineDecomp.exe, vpkeditcli.exe and .fgd in the bin folder where studiomdl.exe is located. 
	For example:
	"C:\Program Files (x86)\Steam\steamapps\common\Source SDK Base 2013 Singleplayer\bin\" (unquote).
	
	2. Open Hammer. Go to “Tools -> Options -> Game Configuration”, add “props_scaling_recompiler.fgd” to the “Game Data files” list and click “OK”. 
	Pay attention to the selected project in the Configuration drop-down list: your project should be there! Add this fgd for all projects where you plan to use the tool!
	
	3. Press F9 (Run Map) and go to Expert mode if you are in Normal mode. 
	Here you need to change the map compilation settings.
	
	4. Click New to add another step.
	If you are using VMFii or any other tool that duplicates your VMF to compile later - put our new item after them. For example if you only use VMFii, our item should be second in the list.
	If you don't use VMFii and other such tools - drag our item to the very beginning.
	
	5. In Command Properties, specify Command. This should be the path to props_scaling_recompiler.exe, for example:
	"C:\Program Files (x86)\Steam\steamapps\common\Source SDK Base 2013 Singleplayer\bin\props_scaling_recompiler.exe" (unquote).
	The path to this file can be specified not manually, but by clicking "Cmds -> Executable" and then selecting our executable file through explorer.
	
	7. Now it is necessary to specify Parameters. Default parameters will be as follows:
	"-game $gamedir -vmf_in $path\$file.vmf -vmf_out $path\psr_temp\$file.vmf -subfolders 1 -force_recompile 0" (unquote).
	It is important to pay attention to the following parameters:
	-vmf_in $path\$file.vmf - this path should be if you don't use VMFii and other programs that create a copy of vmf to change something in it and compile the copy. If you use VMFii and similar tools, you should specify the path to the vmf that is output by the previous tool. For example, “-vmf_in $path\inst_fix\$file.vmf” if the conditional VMFii outputs its vmf to the inst_fix folder.
	-vmf_out $path\psr_temp\$file.vmf - this is the vmf that props_scaling_recompiler will output and which should be specified in the following Compile/run commands!
	-subfolders 1 - put the scaled versions of the props in a separate subfolder (1 = yes, 0 = no).
	-force_recompile 0 - recompile all scaled props that are available on the level (1 = yes, 0 = no). Needed in case the original model has changed, for example.
	
	8. Go through Compile/run commands and specify correct paths in Parameters. It should be the path that props_scaling_recompiler outputs.


Usage example:

	1. Create a new entity.
	
	2. Select its Class prop_static_scalable.
	
	3. The usual setting is taking place. In addition to the usual settings for prop_static we now have Model Scale. We set the scale value as a multiplier, i.e. 2 will mean a 2-fold increase of the prop (the name of the new asset in the content will have the postfix “_scaled_200”, i.e. 200% of the original size).
	Note: this is true for static and most dynamic props, but not for physics props. For some reason physics props are scaled not by N times, but by N^2 times. I.e. a 2x increase will actually increase the model by a factor of 4, model scale 4 will increase the model by a factor of 16. This error is somewhere in the engine. The scaled model will be put into the project content with the name from the "model scale", not the actual scaled values.
	
	4. Compile the map. After the tool has done its work - a new model with a different scale will appear in the content.
	
	5. If the compilation was successful, a scaled version of the model will be waiting for you in the game.


Known issues:

	1. If you have assets with the same name in your content, but in different folders - this may cause a fatal error and “crash” the program. This problem will be fixed in the future.
	
	2. In some cases dynamic and physics props will have incorrect visualization and/or collision. Some of these problems will be fixed in the future, but not all of them. In some cases modification of the original asset will be required.
	
	3. There may be fatal errors when compiling some dynamic props, for example “models/props_c17/door02_double.mdl”. Whether this will be fixed in the future is still unknown.


Future plans:

	1. Fixing critical errors.
	
	2. Upload the source code.
	
	3. Reduce the size of the executable file.
	
	4. New entity - scaling physical props with preserving correct collision and converting any props to physical props.
	
	5. New entity - scaling dynamic props with preserving correct collision and converting any props to dynamic props.
	
	6. Static Prop Combine analog. Merger of several models into one static prop. This functionality is already available in Propper++, but we have some ideas how to improve this.

	
Credits:

	Thanks to ficool2 for Hammer++ https://ficool2.github.io/HammerPlusPlus-Website/
	Thanks to UltraTechX for CrowbarCommandLineDecomp https://github.com/UltraTechX/Crowbar-Command-Line
	Thanks to craftablescience (Laura Lewis) and contributors for vpkeditcli.exe https://github.com/craftablescience/VPKEdit
	Thanks to aptekarr, MyCbEH and v3sp4 for the request, testing and suggestions for improvements.