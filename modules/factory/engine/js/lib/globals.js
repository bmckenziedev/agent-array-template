// Vendored from bench/js/lib/globals.js (agent-array M1 benchmark,); keep the two in step.
'use strict';
// Identifiers a CommonJS module or jest test may use without declaring them.
const JS = `globalThis undefined NaN Infinity eval isFinite isNaN parseFloat parseInt decodeURI
decodeURIComponent encodeURI encodeURIComponent escape unescape Object Function Boolean Symbol Error
AggregateError EvalError RangeError ReferenceError SyntaxError TypeError URIError Number BigInt Math
Date String RegExp Array Int8Array Uint8Array Uint8ClampedArray Int16Array Uint16Array Int32Array
Uint32Array Float32Array Float64Array BigInt64Array BigUint64Array Map Set WeakMap WeakSet WeakRef
FinalizationRegistry ArrayBuffer SharedArrayBuffer Atomics DataView JSON Promise Reflect Proxy Intl
WebAssembly arguments`.split(/\s+/);
const NODE = `require module exports __filename __dirname process Buffer global console setTimeout
clearTimeout setInterval clearInterval setImmediate clearImmediate queueMicrotask structuredClone
URL URLSearchParams TextEncoder TextDecoder AbortController AbortSignal Event EventTarget fetch
Headers Request Response FormData Blob performance crypto btoa atob MessageChannel MessagePort
BroadcastChannel DOMException`.split(/\s+/);
const JEST = `describe it test expect jest beforeEach afterEach beforeAll afterAll fit fdescribe xit
xdescribe xtest pending fail`.split(/\s+/);

const MODULE_GLOBALS = new Set([...JS, ...NODE]);
const TEST_GLOBALS = new Set([...JS, ...NODE, ...JEST]);

const NODE_BUILTINS = new Set(require('module').builtinModules.flatMap((m) => [m, `node:${m}`]));

module.exports = { MODULE_GLOBALS, TEST_GLOBALS, NODE_BUILTINS };
