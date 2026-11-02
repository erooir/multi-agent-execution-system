type Position = [number, number];
type Shape = {
  kind: "point" | "line" | "polygon";
  parts: Position[][];
  name: string;
};
type Geo = {
  type?: string;
  coordinates?: unknown;
  geometries?: Geo[];
  features?: Geo[];
  geometry?: Geo | null;
  properties?: { name?: unknown; 名称?: unknown };
};

// All geometry stays in the supplied coordinate space. No external basemap or projection is used.
export function mapDrawing(geo: Geo | null | undefined) {
  const shapes: Shape[] = [];
  let points = 0;
  let exceeded = false;
  function part(coords: unknown): Position[] {
    if (!Array.isArray(coords)) return [];
    const result: Position[] = [];
    for (const p of coords) {
      if (++points > 20000) {
        exceeded = true;
        break;
      }
      if (!Array.isArray(p) || !Number.isFinite(p[0]) || !Number.isFinite(p[1]))
        return [];
      result.push([p[0], p[1]]);
    }
    return result;
  }
  function add(kind: Shape["kind"], components: unknown[], name: string) {
    const minimum = kind === "point" ? 1 : kind === "line" ? 2 : 4;
    const parts = components.map(part).filter((p) => p.length >= minimum);
    if (parts.length) shapes.push({ kind, parts, name });
  }
  function walk(g: Geo | null | undefined, name: string, depth: number) {
    if (!g || exceeded || depth > 12) return;
    const c = g.coordinates;
    switch (g.type) {
      case "FeatureCollection":
        if (Array.isArray(g.features))
          g.features.forEach((f) => walk(f, name, depth + 1));
        break;
      case "Feature": {
        const label = g.properties?.name ?? g.properties?.名称;
        walk(g.geometry, typeof label === "string" ? label : name, depth + 1);
        break;
      }
      case "GeometryCollection":
        if (Array.isArray(g.geometries))
          g.geometries.forEach((f) => walk(f, name, depth + 1));
        break;
      case "Point":
        add("point", [[c]], name);
        break;
      case "MultiPoint":
        add("point", [c], name);
        break;
      case "LineString":
        add("line", [c], name);
        break;
      case "MultiLineString":
        if (Array.isArray(c)) add("line", c, name);
        break;
      case "Polygon":
        if (Array.isArray(c)) add("polygon", c, name);
        break;
      case "MultiPolygon":
        if (Array.isArray(c))
          c.forEach((p) => {
            if (Array.isArray(p)) add("polygon", p, name);
          });
        break;
    }
  }
  walk(geo, "", 0);
  if (exceeded || !shapes.length) return { marks: [], exceeded };
  const positions = shapes.flatMap((s) => s.parts.flat());
  const x = positions.map((p) => p[0]),
    y = positions.map((p) => p[1]);
  const left = Math.min(...x),
    bottom = Math.min(...y);
  const width = Math.max(...x) - left,
    height = Math.max(...y) - bottom;
  if (!Number.isFinite(width) || !Number.isFinite(height))
    return { marks: [], exceeded: false };
  const scale = Math.min(
    width ? 524 / width : Infinity,
    height ? 184 / height : Infinity,
  );
  const zoom = Number.isFinite(scale) ? scale : 1;
  const tx = (560 - width * zoom) / 2,
    ty = (212 - height * zoom) / 2;
  const marks = shapes.map((s) => ({
    ...s,
    parts: s.parts.map((ps) =>
      ps.map(([px, py]): Position => [
        Math.round((tx + (px - left) * zoom) * 100) / 100,
        Math.round((212 - ty - (py - bottom) * zoom) * 100) / 100,
      ]),
    ),
  }));
  return { marks, exceeded: false };
}
