let canvas, gl, program;
let startTime = Date.now();

const vertexShaderSource = `
attribute vec2 a_position;
void main() {
  gl_Position = vec4(a_position, 0, 1);
}
`;

const fragmentShaderSource = `
precision mediump float;
uniform vec2 iResolution;
uniform float iTime;

void main() {
  vec2 uv = gl_FragCoord.xy / iResolution.xy;
  uv -= 0.5;

  vec3 col = vec3(0.05, 0.05, 0.1);

  for(int i=0;i<6;i++){
    float t = float(i)/6.0;
    float speed = 1.0 + t;
    
    // Vertical wave (moves along y-axis)
    float wave = sin(iTime * speed * 0.2 + uv.y * 8.0) * 0.2;
    
    // Thin line
    float line = 1.0 - smoothstep(0.003, 0.008, abs(uv.x - wave));
    
    // Add blur glow around the line
    float blur = exp(-abs(uv.x - wave) * 20.0) * 0.3;
    
    col += (line + blur) * vec3(0.2+t, 0.3, 0.8);
  }

  gl_FragColor = vec4(col, 1.0);
}
`;

function initShader() {
  canvas = document.getElementById("shaderCanvas");
  gl = canvas.getContext("webgl");

  if (!gl) {
    console.log("WebGL not supported");
    return;
  }

  const vs = gl.createShader(gl.VERTEX_SHADER);
  gl.shaderSource(vs, vertexShaderSource);
  gl.compileShader(vs);
  if (!gl.getShaderParameter(vs, gl.COMPILE_STATUS)) {
    console.error("Vertex shader error:", gl.getShaderInfoLog(vs));
  }

  const fs = gl.createShader(gl.FRAGMENT_SHADER);
  gl.shaderSource(fs, fragmentShaderSource);
  gl.compileShader(fs);
  if (!gl.getShaderParameter(fs, gl.COMPILE_STATUS)) {
    console.error("Fragment shader error:", gl.getShaderInfoLog(fs));
  }

  program = gl.createProgram();
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  gl.useProgram(program);

  const positionBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
    -1,-1, 1,-1, -1,1,
    -1,1, 1,-1, 1,1
  ]), gl.STATIC_DRAW);

  const positionLocation = gl.getAttribLocation(program,"a_position");
  gl.enableVertexAttribArray(positionLocation);
  gl.vertexAttribPointer(positionLocation,2,gl.FLOAT,false,0,0);

  resizeCanvas();
  render();
}

function resizeCanvas(){
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
  gl.viewport(0,0,canvas.width,canvas.height);
}

function render(){
  let time = (Date.now()-startTime)/1000;

  const resLoc = gl.getUniformLocation(program,"iResolution");
  const timeLoc = gl.getUniformLocation(program,"iTime");

  gl.uniform2f(resLoc, canvas.width, canvas.height);
  gl.uniform1f(timeLoc, time);

  gl.drawArrays(gl.TRIANGLES,0,6);
  requestAnimationFrame(render);
}

window.onload = initShader;
window.onresize = resizeCanvas;
