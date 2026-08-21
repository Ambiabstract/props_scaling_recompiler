$modelname	"props/{brace}_static.mdl"
$scale	1.0
$staticprop
$body "body" "static_reference.smd" // "$lod 999 {"
$cdmaterials "models/props/static/"
$texturegroup "SkinFamilies" {
    { "body" }
}
/* A block comment with fake syntax:
   $texturegroup "skinfamilies" { { "wrong" } }
*/
$collisionmodel "static_physics.smd"
{
    $mass 10
    $concave
}
$lod 25 { replacemodel "static_reference.smd" "static_lod1.smd" }
