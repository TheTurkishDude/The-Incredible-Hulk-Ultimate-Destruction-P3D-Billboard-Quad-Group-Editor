Pure3D Billboard Quad Group Editor

A viewer, editor, exporter and importer for Billboard Quad Groups in Radical Entertainment Pure3D (.p3d) files, built and tested against The Incredible Hulk: Ultimate Destruction.

The Tool Is Made By TheTurkishDude

It is a single Python file (p3d_bqg_tool.py) with no required dependencies beyond the standard library.

Features
Lossless .p3d reader/writer. The whole chunk tree is kept as-is, so opening and saving a file without edits gives a byte-identical file. Chunks the tool does not understand are preserved.
Billboard quad group editor. Edit groups and quads: name, shader, size, colour, mode tag, position, rotation (quaternion or Euler), UVs, sort value and bounds. Add, duplicate and delete groups and quads.
3D viewer. Orbit/pan/zoom preview with real textures (additive or alpha blended depending on the shader).
UV panel. Shows the texture with the selected quad's UV polygon drawn over it.
Shader → texture resolution. Follows 0x11000 shader chunks to their 0x19000 texture, across several .p3d files (the shared/global files usually hold the shaders and textures).
Texture support for three platforms. PS2, Xbox and GameCube texture formats (see below).
Texture browser with mip levels and PNG export.
JSON exporter/importer for groups, for scripting and diffing.
Command line tools for listing, exporting, importing, dumping textures and rendering contact sheets.
Requirements
Python 3.8 or newer
tkinter for the GUI (bundled with most Python installs; on Debian/Ubuntu: sudo apt install python3-tk)
numpy (optional) enables the textured 3D preview and --render. Without it the GUI falls back to flat coloured polygons.
pip install numpy
Quick start
python p3d_bqg_tool.py                       # open the GUI
python p3d_bqg_tool.py c01m05.p3d            # open a file directly
python p3d_bqg_tool.py c01m05.p3d global_noncharxbox.p3d   # also load shaders/textures from a shared file

In the GUI use File → Load resource .p3d to add shared files that contain the shaders and textures your groups refer to. Groups whose texture cannot be found are drawn with a red dashed outline and the form tells you which texture is missing.

GUI controls
Action	Control
Orbit	Left mouse drag
Pan	Right mouse drag
Zoom	Mouse wheel
Select group / quad	Tree on the left
Apply an edited field	Enter or leave the field
Open / Save	Ctrl+O / Ctrl+S

The View menu controls texturing, V flip, tinting with the quad colour, the palette layout mode and render resolution. Tools → Texture browser lists every loaded texture.

Command line
python p3d_bqg_tool.py --list      file.p3d [shared.p3d ...]
python p3d_bqg_tool.py --export    file.p3d groups.json
python p3d_bqg_tool.py --import    file.p3d groups.json out.p3d
python p3d_bqg_tool.py --roundtrip file.p3d
python p3d_bqg_tool.py --textures  file.p3d out_dir
python p3d_bqg_tool.py --render    file.p3d sheet.png [shared.p3d ...]
Command	What it does
--list	Prints every billboard quad group and quad. With extra files it also shows which texture each shader resolves to.
--export	Writes all groups to JSON.
--import	Merges groups from JSON into a .p3d and saves a new file. Groups with the same name are replaced, new ones are added.
--roundtrip	Self test: parse and write must reproduce the file byte-for-byte.
--textures	Dumps every texture (top mip level) as PNG.
--render	Renders one tile per group into a contact sheet PNG (needs numpy).
JSON format
json
{
  "format": "p3d-billboard-quad-groups",
  "version": 1,
  "groups": [
    {
      "name": "lighteningElecSiwrlAbomShape",
      "shader": "SS_200050_XXELECDECAYGREY_66B001XX",
      "version": 0,
      "params": [0, 1, 0, 0],
      "group_rotation_xyzw": [0, 0, 0, 1],
      "group_translation": [0, 0, 0],
      "sort_value_0x122000": 0.48,
      "bbox": [0, 0, 0, 0, 0, 0],
      "bsphere": [0, 0, 0, 0],
      "animation_link_hex": null,
      "quads": [
        {
          "name": "lighteningQuadShape",
          "flags": 0,
          "one": 1,
          "mode": "LYAX",
          "colour_argb": "0xFFFFB557",
          "width": 4.87,
          "height": 5.94,
          "distance": 0.0,
          "rotation_xyzw": [0, 0, 0, 1],
          "translation": [0, 0, 0],
          "uv": [[0, 0], [1, 0], [1, 1], [0, 1]],
          "extra_a": [0, 0],
          "extra_b": [0, 0]
        }
      ]
    }
  ]
}

animation_link_hex holds the raw bytes of the linked animation chunk (0x121204) and is passed through unchanged.

File format notes

These notes describe what was observed in the files used to build the tool, not an official specification.

File. "P3D\xFF", u32 headerSize (12), u32 fileSize, then top-level chunks.

Chunk. u32 id, u32 headerSize (includes these 12 bytes), u32 totalSize (header plus children), headerSize-12 bytes of data, then child chunks.

String. u8 length (already padded to a multiple of 4), the characters, zero padded.

Billboard chunks
Chunk	Name	Contents
0x17006	BillboardQuadGroup	u32 version, name, shader name, 5 x u32 (the last one is the quad count), then children
0x17005	BillboardQuad	u32 version, name, u32 flags, u32 one, char[4] mode (AAX, LYAX, NOAX), u32 colour (0xAARRGGBB), f32 width, f32 height, f32 distance
0x17007	Display info	u32 version, quaternion xyzw, translation xyz (used on groups and quads)
0x17009	UV block	u32 version, 6 x vec2: extraA, uv0..uv3, extraB
0x122000	Float	u32 version, f32 (0.45 to 0.5 in the samples; probably a sort or draw value)
0x121204	Animation link	Links to an animation named BQG_<group name>; kept verbatim
0x10003 / 0x10004	Bounds	Bounding box (6 floats) and sphere (4 floats), no version field

Group children appear in this order: 0x17007, quads, 0x122000, optional 0x121204, 0x10003, 0x10004.

Shader to texture chain
group.shader names a 0x11000 shader chunk (for example SS_200050_XXELECDECAYGREY_66B001XX).
The shader has a 0x11002 string parameter with tag TEX giving the texture name, and 0x11003 integer parameters such as BLMD (blend mode; 2 is additive).
The texture is the 0x19000 chunk with that name. Its 0x19001 children are the mip levels, each holding the pixel data in a 0x19002 chunk.
If the shader chunk is missing, the tool guesses the texture from the shader name pattern SS_<n>_XX<TEXTURE>_<hh>B<nnn>XX. This is flagged as "name heuristic".
Texture formats
Platform	0x19002 payload	Notes
PS2	P3DI, 8-bit paletted (format 14)	Palette alpha uses the PS2 range (0 to 0x80) and is doubled. Palette order is PS2 CSM1. Square images are PS2 GS swizzled (PSMT8); non-square images are stored linearly.
PS2	P3DI, 32-bit RGBA (format 27)	Treated as linear. Only a few samples were available.
Xbox	Complete DDS file	DXT1 and DXT5 seen in the samples; DXT3 and uncompressed RGB(A) are also handled. A few textures are PNG.
GameCube	PNG	8-bit paletted with PLTE and tRNS, RGB and RGBA. Some textures are DXT1 DDS.

The PS2 swizzle rule (square = swizzled, non-square = linear) was established by checking that the top mip level, downsampled, matches the next mip level.

Known limitations
No in-game verification. Edited files have not been tested in the game. Keep backups of your originals.
Some field meanings are inferred. The first four u32 values of a group, a quad's flags/one values, the 0x122000 float and the UV extraA/extraB pairs are named from the data, not from documentation.
Preview is approximate. Quads are drawn with their stored orientation (not turned to face the camera), only the first frame of animated textures is shown, and blending follows the shader's BLMD value as additive or alpha.
Runtime-animated quads. Quads with a size of about zero (for example the Hulk gamma punch beams) are scaled by the BQG_ animation in the game. The preview shows them at 2 x 2; the file data is not changed. Quads with a black colour are previewed as white.
Flip V is a guess. It defaults to on; you can toggle it in the View menu.
A few PS2 textures may decode wrongly. ABOMCONTAINER2 (128 x 64) and HELI_PROPS did not match either layout in the mip check. Use View → palette layout to try the other mode.
Renaming a group does not rename its linked BQG_ animation; do that yourself if needed.
JSON import replaces groups of the same name and adds new ones. Use the GUI to delete groups.
Testing
python p3d_bqg_tool.py --roundtrip file.p3d

Checks that parsing and writing the whole file, and re-encoding every billboard group from the typed model, both reproduce the original bytes. This was verified on the PS2 level file (12 groups) and on the Xbox and GameCube global files (31 groups each).

Disclaimer

This is an unofficial fan tool. It is not affiliated with or endorsed by Radical Entertainment, Vivendi/Activision or their successors. The repository contains no game assets; you need your own game files.

The Tool Is Made By TheTurkishDude
