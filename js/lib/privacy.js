const clean = value => String(value ?? "").trim();

export function maskMiddle(value, keepStart = 2, keepEnd = 2) {
  const input = clean(value);
  if (!input) return "";
  if (input.includes("*")) return input.replace(/\*+/g, "***");
  const chars = Array.from(input);
  const start = Math.min(keepStart, Math.floor(chars.length / 2));
  const end = Math.min(keepEnd, chars.length - start - 1);
  return `${chars.slice(0, start).join("")}***${end ? chars.slice(-end).join("") : ""}`;
}

export function maskName(value) {
  const input = clean(value), words = input.split(/\s+/);
  if (input.includes("*") || words.length < 3) return maskMiddle(input);
  return `${words[0]} *** ${words.at(-1)}`;
}

export const maskPhone = value => maskMiddle(value, 3, 3);

export function maskEmail(value) {
  const input = clean(value), at = input.lastIndexOf("@");
  if (input.includes("*") || at < 1) return maskMiddle(input);
  return `${maskMiddle(input.slice(0, at), 2, 1)}${input.slice(at)}`;
}

export function maskAddress(value) {
  const input = clean(value), words = input.split(/\s+/);
  if (input.includes("*") || words.length < 5) return maskMiddle(input);
  return `${words[0]} *** ${words.slice(-3).join(" ")}`;
}
