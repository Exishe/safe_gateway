// The gateway counts offsets in Unicode code points (Python str indices); JavaScript
// strings index UTF-16 units. An emoji before a detection would otherwise shift
// every highlight after it.
export function codePointToUtf16(text: string): (offset: number) => number {
  const units: number[] = [0];
  let position = 0;
  for (const char of text) {
    position += char.length;
    units.push(position);
  }
  return (offset: number) => {
    const unit = units[offset];
    if (unit === undefined || offset < 0) {
      throw new RangeError(`offset ${offset} is outside a text of ${units.length - 1} code points`);
    }
    return unit;
  };
}
