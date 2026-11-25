import { describe, expect, it } from 'vitest';
import type { DicomMetadata } from './api';
import { decodeFrame, defaultWindow, valueRange, windowToRgba } from './dicomPixels';

const meta = (over: Partial<DicomMetadata> = {}): DicomMetadata => ({
  kind: 'dicom', modality: 'CR', study_date: null, body_part: null, study_description: null, rows: 2, columns: 2, frames: 1, bits_allocated: 16, bits_stored: 16,
  samples_per_pixel: 1, photometric: 'MONOCHROME2', pixel_representation: 0, transfer_syntax_uid: '1.2.840.10008.1.2.1', transfer_syntax_name: 'Explicit VR Little Endian',
  window_center: null, window_width: null, rescale_slope: null, rescale_intercept: null, pixel_spacing: null, pixel_offset: 4, pixel_length: 8, viewable: true, viewer_message: null, ...over,
});
function buffer16(values: number[], offset = 4, signed = false) {
  const data = new ArrayBuffer(offset + values.length * 2); const view = new DataView(data);
  values.forEach((value, index) => (signed ? view.setInt16(offset + index * 2, value, true) : view.setUint16(offset + index * 2, value, true)));
  return data;
}

describe('DICOM pixel decoding', () => {
  it('reads unsigned and signed 16-bit little-endian frames at the stored offset', () => {
    expect(Array.from(decodeFrame(buffer16([0, 1000, 2000, 65535]), meta(), 0))).toEqual([0, 1000, 2000, 65535]);
    expect(Array.from(decodeFrame(buffer16([-5, 7, -1000, 3], 4, true), meta({ pixel_representation: 1 }), 0))).toEqual([-5, 7, -1000, 3]);
  });
  it('reads 8-bit data, the right frame of a multi-frame file, and applies rescale', () => {
    const data = new Uint8Array([9, 9, 9, 9, 1, 2, 3, 4, 10, 20, 30, 40]).buffer;
    const eight = meta({ bits_allocated: 8, frames: 2, pixel_length: 8 });
    expect(Array.from(decodeFrame(data, eight, 0))).toEqual([1, 2, 3, 4]);
    expect(Array.from(decodeFrame(data, eight, 1))).toEqual([10, 20, 30, 40]);
    expect(Array.from(decodeFrame(data, meta({ bits_allocated: 8, rescale_slope: 2, rescale_intercept: -1000 }), 0))).toEqual([-998, -996, -994, -992]);
  });
  it('refuses frames outside the file and non-viewable metadata', () => {
    expect(() => decodeFrame(buffer16([1, 2, 3, 4]), meta(), 1)).toThrow();
    expect(() => decodeFrame(buffer16([1, 2, 3]), meta(), 0)).toThrow();
    expect(() => decodeFrame(buffer16([1, 2, 3, 4]), meta({ viewable: false }), 0)).toThrow(/nesuportat/);
  });
});

describe('window and level', () => {
  it('maps values below, inside and above the window to black, gray and white', () => {
    const out = windowToRgba(new Float32Array([0, 500, 1000, 1500, 2000]), 1000, 1000, false);
    const gray = Array.from({ length: 5 }, (_v, index) => out[index * 4]);
    expect(gray[0]).toBe(0); expect(gray[4]).toBe(255);
    expect(gray[1]).toBeLessThan(gray[2]); expect(gray[2]).toBeLessThan(gray[3]);
    expect(Math.abs(gray[2] - 128)).toBeLessThanOrEqual(2);
    expect(out[3]).toBe(255);
  });
  it('a narrower window raises contrast and moving the level shifts the gray', () => {
    const values = new Float32Array([900, 1100]);
    const wide = windowToRgba(values, 1000, 2000, false); const narrow = windowToRgba(values, 1000, 200, false);
    expect(narrow[4] - narrow[0]).toBeGreaterThan(wide[4] - wide[0]);
    expect(windowToRgba(new Float32Array([1000]), 1500, 1000, false)[0]).toBeLessThan(windowToRgba(new Float32Array([1000]), 500, 1000, false)[0]);
  });
  it('MONOCHROME1 is inverted and defaults come from the file or the data range', () => {
    expect(windowToRgba(new Float32Array([0]), 1000, 1000, true)[0]).toBe(255);
    expect(defaultWindow(meta({ window_center: 300, window_width: 600 }), new Float32Array([0, 1]))).toEqual({ center: 300, width: 600 });
    expect(defaultWindow(meta(), new Float32Array([100, 300]))).toEqual({ center: 200, width: 200 });
    expect(valueRange(new Float32Array([3, -2, 9]))).toEqual({ min: -2, max: 9 });
  });
});
