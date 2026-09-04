import { nextTick, onBeforeUnmount } from 'vue'

const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])'
].join(',')

/**
 * 为条件渲染的对话框建立完整键盘焦点生命周期。
 * 背景元素由调用方提供，避免把作为其子节点的对话框一并设为 inert。
 */
export function useDialogFocus(getBackgroundElements) {
  let active = false
  let previousActiveElement = null
  let getDialogElement = () => null
  let getInitialFocusElement = () => null
  let onEscape = () => {}
  let backgroundStates = []

  function focusInitialElement() {
    const dialog = getDialogElement()
    if (!dialog) return
    const initialElement = getInitialFocusElement() || dialog.querySelector(focusableSelector) || dialog
    initialElement.focus()
  }

  function handleKeydown(event) {
    if (!active) return

    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      onEscape()
      return
    }

    if (event.key !== 'Tab') return
    const dialog = getDialogElement()
    if (!dialog) return

    const focusableElements = Array.from(dialog.querySelectorAll(focusableSelector))
      .filter(element => element.getAttribute('aria-hidden') !== 'true')
    if (focusableElements.length === 0) {
      event.preventDefault()
      dialog.focus()
      return
    }

    const firstElement = focusableElements[0]
    const lastElement = focusableElements[focusableElements.length - 1]
    if (!dialog.contains(document.activeElement)) {
      event.preventDefault()
      const fallbackElement = event.shiftKey ? lastElement : firstElement
      fallbackElement.focus()
    } else if (event.shiftKey && document.activeElement === firstElement) {
      event.preventDefault()
      lastElement.focus()
    } else if (!event.shiftKey && document.activeElement === lastElement) {
      event.preventDefault()
      firstElement.focus()
    }
  }

  function activateDialog(dialogElementGetter, initialFocusGetter, escapeHandler) {
    getDialogElement = dialogElementGetter
    getInitialFocusElement = initialFocusGetter || (() => null)
    onEscape = escapeHandler

    if (!active) {
      previousActiveElement = document.activeElement
      const backgroundElements = getBackgroundElements?.() || []
      const elements = Array.isArray(backgroundElements) ? backgroundElements : [backgroundElements]
      backgroundStates = elements.filter(Boolean).map(element => ({
        element,
        inert: element.inert,
        ariaHidden: element.getAttribute('aria-hidden')
      }))
      backgroundStates.forEach(({ element }) => {
        element.inert = true
        element.setAttribute('aria-hidden', 'true')
      })
      document.addEventListener('keydown', handleKeydown)
      active = true
    }

    nextTick(focusInitialElement)
  }

  function deactivateDialog(fallbackFocusGetter = null) {
    if (!active) return

    document.removeEventListener('keydown', handleKeydown)
    backgroundStates.forEach(({ element, inert, ariaHidden }) => {
      element.inert = inert
      if (ariaHidden === null) {
        element.removeAttribute('aria-hidden')
      } else {
        element.setAttribute('aria-hidden', ariaHidden)
      }
    })

    const restoreTarget = previousActiveElement
    active = false
    previousActiveElement = null
    backgroundStates = []
    nextTick(() => {
      const focusTarget = restoreTarget?.isConnected ? restoreTarget : fallbackFocusGetter?.()
      focusTarget?.focus()
    })
  }

  onBeforeUnmount(deactivateDialog)

  return { activateDialog, deactivateDialog }
}
