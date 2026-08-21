// Synthetic dynamic/physics QC. Fake command: $staticprop { ignored }
$modelname "props/example_dynamic.mdl" // keep this comment
$body "body" "example_reference.smd"
$cdmaterials "models/props/example/"

$texturegroup "skinfamilies"
{
	{ "body" "detail" }
	{ "body_alt" "detail_alt" }
}

$collisionjoints "example_physics.smd"
{
	$mass 50
	$inertia 1.00
	$damping 0.00
	$rotdamping 0.00
	$jointconstrain "root" x limit -10 10 0
}

$sequence "idle" "example_reference.smd" fps 30
$lod 40
{
	replacemodel "example_reference.smd" "example_lod1.smd"
}
$lod 120 { replacemodel "example_reference.smd" "example_lod2.smd" }
$shadowlod
{
	replacemodel "example_reference.smd" "blank"
}
$bbox -10 -20 -30 10 20 30
$illumposition 1 2 3
