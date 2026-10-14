// 输入法选字时按回车不发送（教程第 12 条）。组件库的输入框只认 isComposing：Chrome 选字中的回车带着它，组件库已不发；
// Safari（Mac、iPhone）先结束选字（compositionend）再发这次回车的 keydown，isComposing 已是 false，只剩 keyCode 229 表明
// 这一下是输入法用掉的 —— 没拦就把还没打完的半句发出去了。

type KeyLike = { key: string; keyCode?: number; nativeEvent?: { isComposing?: boolean; keyCode?: number } };

/** 这次回车是输入法选字用掉的，不是要发送。 */
export function isImeEnter(event: KeyLike): boolean {
  if (event.key !== "Enter") return false;
  return event.nativeEvent?.isComposing === true || event.keyCode === 229 || event.nativeEvent?.keyCode === 229;
}

/** 输入框的 onKeyDown（排在组件库自己的处理前面）：输入法的回车标成已处理，组件库就不再当发送。
 *  Chrome 选字中（isComposing）组件库本来就不发，这里不动它，免得影响输入法上屏。 */
export function holdImeEnter(event: KeyLike & { preventDefault(): void }): void {
  if (isImeEnter(event) && event.nativeEvent?.isComposing !== true) event.preventDefault();
}
