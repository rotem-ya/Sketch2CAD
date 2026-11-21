# Pascal Editor — scan to an editable model (prototype)

Pascal Editor is the open building editor at <https://github.com/pascalorg/editor> (MIT). It runs in
the browser, from `npx @pascal-app/cli editor`, or in Docker, and it speaks MCP. A scan is a
reference layer. Walls, doors, windows, slabs and ceilings stay editable. IFC comes back out
through `@pascal-app/ifc-converter`.

Sketch2CAD does not embed Pascal and does not call it. `sketch2cad.io.pascal_bim.write_package`
writes four files into a folder:

| file | what it is |
|---|---|
| `spec.json` | the Sketch2CAD drawing |
| `model.ifc` | the same walls, openings, floors, ceilings and dimensions as IFC4 |
| `scene.json` | Pascal nodes in metres: `site` / `building` / `level` / `wall` / `door` / `window` / `slab` / `ceiling` / `scan` |
| `connection.json` | the public CLI and MCP command. No API key, token or password |

Plan coordinates in the scene are Pascal's `[x, z]`. Their Y is up. A door or window is a child of
its wall, and `position` is the centre in that wall's coordinates (`[along, sill + height/2, 0]`),
which is what Pascal's schema calls the wall-local centre. Level `height` is the distance up to the
next storey. `baseElevation` is only the leftover offset above that stack.

To open the editor locally (Pascal documents macOS; Linux is the Docker image):

```bash
npx @pascal-app/cli editor --no-open
```

MCP, still with no key stored in this repository:

```json
{"mcpServers": {"pascal": {"command": "npx", "args": ["--yes", "--package=@pascal-app/cli", "pascal", "mcp", "connect"]}}}
```

The local connector reads `~/.pascal/run` itself. A hosted workspace is a separate account the
person signs in to; this prototype does not start one.

A scan is recorded as `scan.url` only when Pascal would accept it: `https://`, `asset://`,
`artifact://` and a 64-digit hash, `blob:`, `data:image/`, an app-relative `/path`, or
`http://localhost`. A disk path such as `D:\scans\as-built.e57` is kept in
`metadata.sketch2cad.sourcePath` and has to be imported in the editor. `file:` and `javascript:`
are not written into `scan.url`.

`spec_from_scene` reads a scene this module wrote back into a spec, in the units saved on the site.

אב-טיפוס: סריקה כהפניה, ואז קירות, דלתות, חלונות, רצפות ותקרות, ו-IFC לידם. בלי מפתח ובלי רשת.
