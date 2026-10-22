/**
 * LAN development pages are often served over HTTP, where Clipboard API access
 * may be unavailable. Keep a DOM fallback for those browser contexts.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Continue to the synchronous browser fallback below.
  }

  const input = document.createElement("textarea");
  input.value = text;
  input.setAttribute("readonly", "");
  input.setAttribute("aria-hidden", "true");
  input.style.position = "fixed";
  input.style.opacity = "0";
  input.style.pointerEvents = "none";
  document.body.appendChild(input);
  const selection = document.getSelection();
  const previousRange =
    selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null;
  input.select();
  input.setSelectionRange(0, text.length);
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    input.remove();
    if (selection && previousRange) {
      selection.removeAllRanges();
      selection.addRange(previousRange);
    }
  }
}
