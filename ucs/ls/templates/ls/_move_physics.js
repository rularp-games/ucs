// Физика «Строя»: копия engine.MoveSim и engine.move_window строка в строку.
// Шаг фиксированный (1/fps), дрейф из mulberry32(seed), толчки и сбой тяги заданы в задаче.
// Порядок арифметических операций совпадает с Python, поэтому числа получаются одинаковые
// и сервер, прогнав запись нажатий, приходит к тому же итогу, что и экран.
// Файл подключается в шаблон модели и читается тестом через node, поэтому без шаблонных тегов.
function lsMulberry32(seed) {
  var a = seed | 0;
  return function () {
    a = a + 0x6D2B79F5 | 0;
    var t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

function lsMoveWindow(p, n) {
  var t = (n + 1) / p.fps;
  var center = 0.0;
  if (p.amplitude) {
    var phase = t / p.period;
    phase = phase - Math.floor(phase);
    var q;
    if (phase < 0.5) {
      q = phase * 2;
      center = p.amplitude * (4 * q * (1 - q));
    } else {
      q = (phase - 0.5) * 2;
      center = p.amplitude * (-4 * q * (1 - q));
    }
  }
  var half = p.half;
  if (p.narrow) {
    var start = p.steps / p.fps - p.narrow_seconds;
    if (t > start) {
      half = p.half + (p.narrow - p.half) * ((t - start) / p.narrow_seconds);
    }
  }
  return [center, half];
}

function LsMoveSim(p) {
  this.p = p;
  this.rand = lsMulberry32(p.seed);
  this.dt = 1 / p.fps;
  this.pushes = {};
  for (var i = 0; i < p.pushes.length; i++) this.pushes[p.pushes[i][0]] = p.pushes[i][1];
  this.history = [];
  this.n = 0;
  this.x = 0.0;
  this.v = 0.0;
  this.angle = 0.0;
  this.drift = p.drift0;
}

LsMoveSim.GLITCH_DELAY = 9;

LsMoveSim.prototype.step = function (u) {
  var p = this.p, dt = this.dt, n = this.n;
  this.history.push(u);
  var drift = this.drift + (this.rand() - 0.5) * p.drift_step * dt;
  if (drift > p.drift_max) drift = p.drift_max;
  if (drift < -p.drift_max) drift = -p.drift_max;
  this.drift = drift;
  var g = p.glitch;
  if (g && g.start <= n && n < g.start + g.steps) {
    if (g.type === 'invert') u = -u;
    else u = n >= LsMoveSim.GLITCH_DELAY ? this.history[n - LsMoveSim.GLITCH_DELAY] : 0;
  }
  var angle = this.angle;
  if (u !== 0) angle = angle + u * p.tilt_rate * dt;
  else angle = angle - angle * p.tilt_return * dt;
  if (angle > 1) angle = 1.0;
  if (angle < -1) angle = -1.0;
  this.angle = angle;
  var v = this.v;
  if (Object.prototype.hasOwnProperty.call(this.pushes, n)) v = v + this.pushes[n] * p.push;
  v = v + (angle * p.gravity + drift) * dt;
  v = v - v * p.damp * dt;
  this.v = v;
  this.x = this.x + v * dt;
  var w = lsMoveWindow(p, n);
  this.n = n + 1;
  return Math.abs(this.x - w[0]) <= w[1];
};

if (typeof module !== 'undefined') module.exports = { LsMoveSim: LsMoveSim, lsMoveWindow: lsMoveWindow, lsMulberry32: lsMulberry32 };
