import test from "node:test";
import assert from "node:assert/strict";
import { mapDrawing } from "../src/questionMap.ts";

test("地图用原始资料坐标绘制点和带内环的多边形", () => {
  const { marks, exceeded } = mapDrawing({
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: { name: "样点" },
        geometry: { type: "Point", coordinates: [0, 0] },
      },
      {
        type: "Feature",
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [0, 0],
              [4, 0],
              [4, 4],
              [0, 0],
            ],
            [
              [1, 1],
              [1.5, 1],
              [2, 2],
              [1, 1],
            ],
          ],
        },
      },
    ],
  });
  assert.equal(exceeded, false);
  assert.equal(marks.length, 2);
  assert.equal(marks[0].name, "样点");
  assert.equal(marks[1].parts.length, 2);
  for (const { parts } of marks)
    for (const line of parts)
      for (const [x, y] of line) {
        assert.ok(x >= 0 && x <= 560 && y >= 0 && y <= 212);
      }
});

test("空、无效、超出预览范围的图层不生成占位几何", () => {
  assert.deepEqual(mapDrawing(null), { marks: [], exceeded: false });
  assert.deepEqual(
    mapDrawing({
      type: "LineString",
      coordinates: [
        [12, "bad"],
        [3, 5],
      ],
    }),
    { marks: [], exceeded: false },
  );
  assert.deepEqual(
    mapDrawing({ type: "MultiPoint", coordinates: Array(20001).fill([1, 2]) }),
    { marks: [], exceeded: true },
  );
});
