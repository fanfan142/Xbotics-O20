class O20UrdfViewer {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.options = options;
    this.gl = canvas.getContext("webgl", { antialias: true, alpha: true });
    if (!this.gl) throw new Error("WebGL is not available");
    this.program = createProgram(this.gl);
    this.links = new Map();
    this.joints = [];
    this.children = new Map();
    this.meshCache = new Map();
    this.linkMeshes = new Map();
    this.rootLink = "";
    this.positions = Array(16).fill(0);
    this.ready = false;
    this.loading = false;
    this.error = "";
    this.bounds = emptyBounds();
    this.side = options.side === "left" ? "left" : "right";
    this.defaultYaw = options.yaw ?? -0.75;
    this.leftYaw = options.leftYaw ?? this.defaultYaw + Math.PI;
    this.yaw = this.side === "left" ? this.leftYaw : this.defaultYaw;
    this.pitch = options.pitch ?? 0.45;
    this.distance = options.distance ?? 0.42;
    this.target = [0, 0, 0.07];
    this.autoRotate = Boolean(options.autoRotate);
    this._bindOrbit();
  }

  async load() {
    if (this.loading || this.ready) return;
    this.loading = true;
    try {
      const model = await fetchJson("/api/urdf/model");
      this.meshBase = model.model.mesh_base;
      this._parseUrdf(model.model.urdf);
      await this._loadMeshes();
      this.ready = true;
    } catch (error) {
      this.error = String(error.message || error);
      throw error;
    } finally {
      this.loading = false;
    }
  }

  setJointPositions(positions) {
    if (Array.isArray(positions)) {
      const normalized = positions.slice(0, 16).map((value) => Number(value) || 0);
      while (normalized.length < 16) normalized.push(0);
      this.positions = normalized;
    }
  }

  setSide(side) {
    this.side = side === "left" ? "left" : "right";
    this.yaw = this.side === "left" ? this.leftYaw : this.defaultYaw;
  }

  render(time = 0) {
    const gl = this.gl;
    const rect = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.floor(rect.width * dpr));
    const height = Math.max(1, Math.floor(rect.height * dpr));
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }

    gl.viewport(0, 0, width, height);
    gl.enable(gl.DEPTH_TEST);
    gl.disable(gl.CULL_FACE);
    gl.clearColor(0.045, 0.043, 0.038, 0.0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    if (!this.ready) return;

    const aspect = width / height;
    const projection = mat4Perspective(Math.PI / 4.2, aspect, 0.005, 4);
    const orbitYaw = this.yaw + (this.autoRotate ? Math.sin(time / 8000) * 0.12 : 0);
    const eye = orbitEye(this.target, this.distance, orbitYaw, this.pitch);
    const view = mat4LookAt(eye, this.target, [0, 0, 1]);
    const vp = mat4Multiply(projection, view);
    const mirror = this.side === "left" ? mat4Scale([-1, 1, 1]) : mat4Identity();
    const rootPose = mat4Multiply(mirror, mat4RotationX(this.options.rootPitch || 0));

    gl.useProgram(this.program.handle);
    gl.uniform3f(this.program.uniforms.lightDir, -0.35, -0.55, 0.76);
    gl.uniform3f(this.program.uniforms.viewPos, eye[0], eye[1], eye[2]);

    this._drawLink(this.rootLink, rootPose, vp);
  }

  _parseUrdf(urdfText) {
    const doc = new DOMParser().parseFromString(urdfText, "application/xml");
    const childLinks = new Set();

    doc.querySelectorAll("link").forEach((linkEl) => {
      const name = linkEl.getAttribute("name");
      const visual = linkEl.querySelector("visual");
      const mesh = visual?.querySelector("mesh");
      const color = visual?.querySelector("color")?.getAttribute("rgba");
      const origin = parseOrigin(visual?.querySelector("origin"));
      this.links.set(name, {
        name,
        visualOrigin: origin,
        meshFile: mesh?.getAttribute("filename") || "",
        color: parseColor(color, colorForLink(name)),
      });
    });

    doc.querySelectorAll("joint").forEach((jointEl) => {
      const parent = jointEl.querySelector("parent")?.getAttribute("link") || "";
      const child = jointEl.querySelector("child")?.getAttribute("link") || "";
      const joint = {
        name: jointEl.getAttribute("name") || "",
        type: jointEl.getAttribute("type") || "fixed",
        parent,
        child,
        origin: parseOrigin(jointEl.querySelector("origin")),
        axis: parseVec(jointEl.querySelector("axis")?.getAttribute("xyz"), [0, 0, 1]),
        lower: Number(jointEl.querySelector("limit")?.getAttribute("lower") || 0),
        upper: Number(jointEl.querySelector("limit")?.getAttribute("upper") || 0),
      };
      this.joints.push(joint);
      childLinks.add(child);
      if (!this.children.has(parent)) this.children.set(parent, []);
      this.children.get(parent).push(joint);
    });

    this.rootLink = [...this.links.keys()].find((name) => !childLinks.has(name)) || "hand_link";
  }

  async _loadMeshes() {
    const meshFiles = new Set();
    for (const link of this.links.values()) {
      if (link.meshFile) meshFiles.add(link.meshFile);
    }
    await Promise.all([...meshFiles].map(async (meshFile) => {
      const url = meshUrl(meshFile, this.meshBase);
      const buffer = await fetch(url).then((response) => {
        if (!response.ok) throw new Error(`mesh load failed: ${url}`);
        return response.arrayBuffer();
      });
      const mesh = parseStl(buffer);
      this.meshCache.set(meshFile, uploadMesh(this.gl, mesh));
    }));
    for (const link of this.links.values()) {
      if (link.meshFile && this.meshCache.has(link.meshFile)) {
        this.linkMeshes.set(link.name, this.meshCache.get(link.meshFile));
        mergeBounds(this.bounds, this.meshCache.get(link.meshFile).bounds);
      }
    }
  }

  _drawLink(linkName, parentWorld, vp) {
    const link = this.links.get(linkName);
    if (!link) return;
    const visualWorld = mat4Multiply(parentWorld, transformFromOrigin(link.visualOrigin));
    const mesh = this.linkMeshes.get(linkName);
    if (mesh) this._drawMesh(mesh, visualWorld, vp, link.color);

    const joints = this.children.get(linkName) || [];
    for (const joint of joints) {
      const jointOrigin = transformFromOrigin(joint.origin);
      const jointMotion = joint.type === "fixed"
        ? mat4Identity()
        : mat4AxisAngle(joint.axis, this._jointAngle(joint));
      const childWorld = mat4Multiply(parentWorld, mat4Multiply(jointOrigin, jointMotion));
      this._drawLink(joint.child, childWorld, vp);
    }
  }

  _drawMesh(mesh, world, vp, color) {
    const gl = this.gl;
    const mvp = mat4Multiply(vp, world);
    gl.uniformMatrix4fv(this.program.uniforms.mvp, false, mvp);
    gl.uniformMatrix4fv(this.program.uniforms.model, false, world);
    gl.uniformMatrix3fv(this.program.uniforms.normalMatrix, false, mat3FromMat4(world));
    gl.uniform4f(this.program.uniforms.color, color[0], color[1], color[2], color[3]);

    gl.bindBuffer(gl.ARRAY_BUFFER, mesh.positionBuffer);
    gl.enableVertexAttribArray(this.program.attribs.position);
    gl.vertexAttribPointer(this.program.attribs.position, 3, gl.FLOAT, false, 0, 0);

    gl.bindBuffer(gl.ARRAY_BUFFER, mesh.normalBuffer);
    gl.enableVertexAttribArray(this.program.attribs.normal);
    gl.vertexAttribPointer(this.program.attribs.normal, 3, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLES, 0, mesh.count);
  }

  _jointAngle(joint) {
    const mapped = mapO20Joint(joint.name, this.positions);
    if (mapped === null) return 0;
    return Math.max(joint.lower, Math.min(joint.upper, mapped));
  }

  _bindOrbit() {
    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    this.canvas.addEventListener("pointerdown", (event) => {
      dragging = true;
      lastX = event.clientX;
      lastY = event.clientY;
      this.canvas.setPointerCapture(event.pointerId);
    });
    this.canvas.addEventListener("pointermove", (event) => {
      if (!dragging) return;
      const dx = event.clientX - lastX;
      const dy = event.clientY - lastY;
      lastX = event.clientX;
      lastY = event.clientY;
      this.yaw -= dx * 0.006;
      this.pitch = clamp(this.pitch + dy * 0.005, -1.15, 1.25);
    });
    this.canvas.addEventListener("pointerup", () => {
      dragging = false;
    });
    this.canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      this.distance = clamp(this.distance * (1 + event.deltaY * 0.001), 0.22, 0.95);
    }, { passive: false });
  }
}

function mapO20Joint(name, p) {
  const deg = Math.PI / 180;
  const linear = (value, minIn, maxIn, minOut, maxOut) => {
    const t = clamp((value - minIn) / (maxIn - minIn), 0, 1);
    return minOut + (maxOut - minOut) * t;
  };
  const mapping = {
    thumb_cmc_roll: linear(p[2] ?? 0, 0, 180, -0.52, 0.52),
    thumb_cmc_yaw: linear(p[3] ?? 0, 0, 130, 1.57, -0.63),
    thumb_cmc_pitch: linear(p[0] ?? 0, 0, 120, 0, 1.17),
    thumb_mcp: linear(p[1] ?? 0, 0, 150, 0, 1.57),
    index_mcp_roll: linear(p[4] ?? 0, -30, 30, -0.35, 0.09),
    index_mcp_pitch: (p[5] ?? 0) * deg,
    index_dip: (p[6] ?? 0) * deg,
    middle_mcp_roll: linear(p[7] ?? 0, -30, 30, -0.26, 0.26),
    middle_mcp_pitch: (p[8] ?? 0) * deg,
    middle_dip: (p[9] ?? 0) * deg,
    ring_mcp_roll: linear(p[10] ?? 0, -20, 20, -0.2, 0.2),
    ring_mcp_pitch: (p[11] ?? 0) * deg,
    ring_dip: (p[12] ?? 0) * deg,
    pinky_mcp_roll: linear(p[13] ?? 0, -20, 20, -0.21, 0.09),
    pinky_mcp_pitch: (p[14] ?? 0) * deg,
    pinky_dip: (p[15] ?? 0) * deg,
  };
  return Object.prototype.hasOwnProperty.call(mapping, name) ? mapping[name] : null;
}

function createProgram(gl) {
  const vertexSource = `
    attribute vec3 aPosition;
    attribute vec3 aNormal;
    uniform mat4 uMvp;
    uniform mat4 uModel;
    uniform mat3 uNormalMatrix;
    varying vec3 vNormal;
    varying vec3 vWorld;
    void main() {
      vec4 world = uModel * vec4(aPosition, 1.0);
      vWorld = world.xyz;
      vNormal = normalize(uNormalMatrix * aNormal);
      gl_Position = uMvp * vec4(aPosition, 1.0);
    }
  `;
  const fragmentSource = `
    precision mediump float;
    varying vec3 vNormal;
    varying vec3 vWorld;
    uniform vec4 uColor;
    uniform vec3 uLightDir;
    uniform vec3 uViewPos;
    void main() {
      vec3 n = normalize(vNormal);
      vec3 l = normalize(uLightDir);
      vec3 v = normalize(uViewPos - vWorld);
      float diffuse = max(dot(n, l), 0.0);
      vec3 h = normalize(l + v);
      float spec = pow(max(dot(n, h), 0.0), 36.0) * 0.22;
      vec3 color = uColor.rgb * (0.34 + diffuse * 0.72) + vec3(spec);
      gl_FragColor = vec4(color, uColor.a);
    }
  `;
  const handle = gl.createProgram();
  gl.attachShader(handle, compileShader(gl, gl.VERTEX_SHADER, vertexSource));
  gl.attachShader(handle, compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource));
  gl.linkProgram(handle);
  if (!gl.getProgramParameter(handle, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(handle) || "shader link failed");
  }
  return {
    handle,
    attribs: {
      position: gl.getAttribLocation(handle, "aPosition"),
      normal: gl.getAttribLocation(handle, "aNormal"),
    },
    uniforms: {
      mvp: gl.getUniformLocation(handle, "uMvp"),
      model: gl.getUniformLocation(handle, "uModel"),
      normalMatrix: gl.getUniformLocation(handle, "uNormalMatrix"),
      color: gl.getUniformLocation(handle, "uColor"),
      lightDir: gl.getUniformLocation(handle, "uLightDir"),
      viewPos: gl.getUniformLocation(handle, "uViewPos"),
    },
  };
}

function compileShader(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error(gl.getShaderInfoLog(shader) || "shader compile failed");
  }
  return shader;
}

function parseStl(buffer) {
  const view = new DataView(buffer);
  const triCount = view.byteLength >= 84 ? view.getUint32(80, true) : 0;
  const expected = 84 + triCount * 50;
  if (triCount > 0 && expected === view.byteLength) {
    return parseBinaryStl(view, triCount, 84);
  }
  const embedded = findEmbeddedBinaryStl(view);
  if (embedded) {
    return parseBinaryStl(view, embedded.triCount, embedded.offset);
  }
  return parseAsciiStl(new TextDecoder().decode(buffer));
}

function findEmbeddedBinaryStl(view) {
  let first = 0;
  while (first < view.byteLength && view.getUint8(first) === 0) first++;
  for (let offset = first; offset <= view.byteLength - 4; offset++) {
    const triCount = view.getUint32(offset, true);
    if (triCount <= 0) continue;
    if (offset + 4 + triCount * 50 === view.byteLength) {
      return { offset: offset + 4, triCount };
    }
  }
  return null;
}

function parseBinaryStl(view, triCount, dataOffset = 84) {
  const positions = new Float32Array(triCount * 9);
  const normals = new Float32Array(triCount * 9);
  const bounds = emptyBounds();
  let offset = dataOffset;
  let cursor = 0;
  for (let i = 0; i < triCount; i++) {
    let normal = [
      view.getFloat32(offset, true),
      view.getFloat32(offset + 4, true),
      view.getFloat32(offset + 8, true),
    ];
    offset += 12;
    const face = [];
    for (let v = 0; v < 3; v++) {
      const vertex = [
        view.getFloat32(offset, true),
        view.getFloat32(offset + 4, true),
        view.getFloat32(offset + 8, true),
      ];
      offset += 12;
      face.push(vertex);
      expandBounds(bounds, vertex);
      positions[cursor + v * 3] = vertex[0];
      positions[cursor + v * 3 + 1] = vertex[1];
      positions[cursor + v * 3 + 2] = vertex[2];
    }
    if (length3(normal) < 0.00001) normal = faceNormal(face[0], face[1], face[2]);
    for (let v = 0; v < 3; v++) {
      normals[cursor + v * 3] = normal[0];
      normals[cursor + v * 3 + 1] = normal[1];
      normals[cursor + v * 3 + 2] = normal[2];
    }
    cursor += 9;
    offset += 2;
  }
  return { positions, normals, count: triCount * 3, bounds };
}

function parseAsciiStl(text) {
  const vertices = [];
  const normals = [];
  let currentNormal = [0, 0, 1];
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (line.startsWith("facet normal")) {
      currentNormal = line.split(/\s+/).slice(2, 5).map(Number);
    }
    if (line.startsWith("vertex")) {
      const vertex = line.split(/\s+/).slice(1, 4).map(Number);
      vertices.push(vertex);
      normals.push(currentNormal);
    }
  }
  const positions = new Float32Array(vertices.length * 3);
  const normalArray = new Float32Array(vertices.length * 3);
  const bounds = emptyBounds();
  vertices.forEach((vertex, index) => {
    expandBounds(bounds, vertex);
    positions.set(vertex, index * 3);
    normalArray.set(normals[index], index * 3);
  });
  return { positions, normals: normalArray, count: vertices.length, bounds };
}

function uploadMesh(gl, mesh) {
  const positionBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, mesh.positions, gl.STATIC_DRAW);
  const normalBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, normalBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, mesh.normals, gl.STATIC_DRAW);
  return {
    positionBuffer,
    normalBuffer,
    count: mesh.count,
    bounds: mesh.bounds,
  };
}

function meshUrl(filename, meshBase) {
  const marker = "/meshes/";
  const index = filename.lastIndexOf(marker);
  const file = index >= 0 ? filename.slice(index + marker.length) : filename.split("/").pop();
  return meshBase + encodeURIComponent(file).replaceAll("%2F", "/");
}

function parseOrigin(originEl) {
  return {
    xyz: parseVec(originEl?.getAttribute("xyz"), [0, 0, 0]),
    rpy: parseVec(originEl?.getAttribute("rpy"), [0, 0, 0]),
  };
}

function parseVec(value, fallback) {
  if (!value) return [...fallback];
  const parsed = value.trim().split(/\s+/).map(Number);
  return parsed.length === 3 && parsed.every(Number.isFinite) ? parsed : [...fallback];
}

function parseColor(value, fallback) {
  if (!value) return fallback;
  const parsed = value.trim().split(/\s+/).map(Number);
  if (parsed.length >= 3 && parsed.every(Number.isFinite)) {
    return [parsed[0], parsed[1], parsed[2], parsed[3] ?? 1];
  }
  return fallback;
}

function colorForLink(name) {
  if (name.includes("thumb")) return [0.86, 0.73, 0.53, 1];
  if (name.includes("index")) return [0.78, 0.86, 0.88, 1];
  if (name.includes("middle")) return [0.83, 0.83, 0.78, 1];
  if (name.includes("ring")) return [0.74, 0.82, 0.76, 1];
  if (name.includes("pinky")) return [0.82, 0.7, 0.74, 1];
  return [0.9, 0.9, 0.86, 1];
}

function transformFromOrigin(origin) {
  return mat4Multiply(mat4Translation(origin.xyz), mat4FromRpy(origin.rpy));
}

function mat4Identity() {
  return new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
}

function mat4Multiply(a, b) {
  const out = new Float32Array(16);
  for (let col = 0; col < 4; col++) {
    for (let row = 0; row < 4; row++) {
      out[col * 4 + row] =
        a[0 * 4 + row] * b[col * 4 + 0] +
        a[1 * 4 + row] * b[col * 4 + 1] +
        a[2 * 4 + row] * b[col * 4 + 2] +
        a[3 * 4 + row] * b[col * 4 + 3];
    }
  }
  return out;
}

function mat4Translation(v) {
  const out = mat4Identity();
  out[12] = v[0];
  out[13] = v[1];
  out[14] = v[2];
  return out;
}

function mat4Scale(v) {
  const out = mat4Identity();
  out[0] = v[0];
  out[5] = v[1];
  out[10] = v[2];
  return out;
}

function mat4RotationX(a) {
  const c = Math.cos(a);
  const s = Math.sin(a);
  return new Float32Array([1, 0, 0, 0, 0, c, s, 0, 0, -s, c, 0, 0, 0, 0, 1]);
}

function mat4RotationY(a) {
  const c = Math.cos(a);
  const s = Math.sin(a);
  return new Float32Array([c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1]);
}

function mat4RotationZ(a) {
  const c = Math.cos(a);
  const s = Math.sin(a);
  return new Float32Array([c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
}

function mat4FromRpy(rpy) {
  return mat4Multiply(mat4RotationZ(rpy[2]), mat4Multiply(mat4RotationY(rpy[1]), mat4RotationX(rpy[0])));
}

function mat4AxisAngle(axis, angle) {
  const n = normalize3(axis);
  const x = n[0];
  const y = n[1];
  const z = n[2];
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  const t = 1 - c;
  return new Float32Array([
    t * x * x + c, t * x * y + s * z, t * x * z - s * y, 0,
    t * x * y - s * z, t * y * y + c, t * y * z + s * x, 0,
    t * x * z + s * y, t * y * z - s * x, t * z * z + c, 0,
    0, 0, 0, 1,
  ]);
}

function mat4Perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2);
  const nf = 1 / (near - far);
  return new Float32Array([
    f / aspect, 0, 0, 0,
    0, f, 0, 0,
    0, 0, (far + near) * nf, -1,
    0, 0, 2 * far * near * nf, 0,
  ]);
}

function mat4LookAt(eye, center, up) {
  const z = normalize3(sub3(eye, center));
  const x = normalize3(cross3(up, z));
  const y = cross3(z, x);
  return new Float32Array([
    x[0], y[0], z[0], 0,
    x[1], y[1], z[1], 0,
    x[2], y[2], z[2], 0,
    -dot3(x, eye), -dot3(y, eye), -dot3(z, eye), 1,
  ]);
}

function mat3FromMat4(m) {
  return new Float32Array([m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]]);
}

function orbitEye(target, distance, yaw, pitch) {
  const cp = Math.cos(pitch);
  return [
    target[0] + Math.cos(yaw) * cp * distance,
    target[1] + Math.sin(yaw) * cp * distance,
    target[2] + Math.sin(pitch) * distance,
  ];
}

function faceNormal(a, b, c) {
  return normalize3(cross3(sub3(b, a), sub3(c, a)));
}

function normalize3(v) {
  const len = length3(v) || 1;
  return [v[0] / len, v[1] / len, v[2] / len];
}

function length3(v) {
  return Math.hypot(v[0], v[1], v[2]);
}

function sub3(a, b) {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function cross3(a, b) {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
}

function dot3(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function emptyBounds() {
  return { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
}

function expandBounds(bounds, vertex) {
  for (let i = 0; i < 3; i++) {
    bounds.min[i] = Math.min(bounds.min[i], vertex[i]);
    bounds.max[i] = Math.max(bounds.max[i], vertex[i]);
  }
}

function mergeBounds(a, b) {
  for (let i = 0; i < 3; i++) {
    a.min[i] = Math.min(a.min[i], b.min[i]);
    a.max[i] = Math.max(a.max[i], b.max[i]);
  }
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} ${response.status}`);
  return response.json();
}

window.O20UrdfViewer = O20UrdfViewer;
