"""NVDA Tools-menu editor for Modern Mono's engine dictionary."""

import wx

import globalPluginHandler
import gui
import synthDriverHandler


def _modernMono():
	synth = synthDriverHandler.getSynth()
	if synth is None or getattr(synth, "name", None) != "modernmono":
		raise RuntimeError("Select the Modern Mono synthesizer first.")
	return synth


class DictionaryDialog(wx.Dialog):
	def __init__(self, parent):
		super().__init__(parent, title="Modern Mono user dictionary")
		panel = wx.Panel(self)
		outer = wx.BoxSizer(wx.VERTICAL)

		outer.Add(wx.StaticText(panel, label="&Entries"), 0, wx.BOTTOM, 3)
		self.entries = wx.ListBox(panel, style=wx.LB_SINGLE)
		outer.Add(self.entries, 1, wx.EXPAND | wx.BOTTOM, 8)

		outer.Add(wx.StaticText(panel, label="&Spelling or phrase"), 0, wx.BOTTOM, 3)
		self.spelling = wx.TextCtrl(panel)
		outer.Add(self.spelling, 0, wx.EXPAND | wx.BOTTOM, 8)

		outer.Add(wx.StaticText(panel, label="&Raw Monolog phonetics"), 0, wx.BOTTOM, 3)
		self.phonetics = wx.TextCtrl(panel)
		outer.Add(self.phonetics, 0, wx.EXPAND | wx.BOTTOM, 8)

		buttons = wx.BoxSizer(wx.HORIZONTAL)
		preview = wx.Button(panel, label="&Preview")
		save = wx.Button(panel, label="&Save or update")
		delete = wx.Button(panel, label="&Delete")
		close = wx.Button(panel, wx.ID_CLOSE)
		for button in (preview, save, delete, close):
			buttons.Add(button, 0, wx.RIGHT, 6)
		outer.Add(buttons, 0, wx.ALIGN_RIGHT)
		panel.SetSizer(outer)

		self.entries.Bind(wx.EVT_LISTBOX, self._onSelect)
		preview.Bind(wx.EVT_BUTTON, self._onPreview)
		save.Bind(wx.EVT_BUTTON, self._onSave)
		delete.Bind(wx.EVT_BUTTON, self._onDelete)
		close.Bind(wx.EVT_BUTTON, lambda event: self.EndModal(wx.ID_CLOSE))
		self.SetSize((620, 440))
		self.CentreOnParent()
		self._reload()

	def _showError(self, error):
		wx.MessageBox(str(error), "Modern Mono", wx.OK | wx.ICON_ERROR, self)

	def _reload(self, select=None):
		try:
			self._values = _modernMono().getUserDictionaryEntries()
		except Exception as error:
			self._showError(error)
			self._values = {}
		words = sorted(self._values)
		self.entries.Set(words)
		if select in self._values:
			self.entries.SetStringSelection(select)

	def _onSelect(self, event):
		word = self.entries.GetStringSelection()
		self.spelling.SetValue(word)
		self.phonetics.SetValue(self._values.get(word, ""))

	def _onPreview(self, event):
		try:
			_modernMono().previewPhonetics(self.phonetics.GetValue().strip())
		except Exception as error:
			self._showError(error)

	def _onSave(self, event):
		word = self.spelling.GetValue().strip()
		try:
			_modernMono().setUserDictionaryEntry(word, self.phonetics.GetValue())
		except Exception as error:
			self._showError(error)
			return
		self._reload(word.casefold())

	def _onDelete(self, event):
		word = self.spelling.GetValue().strip()
		if not word:
			return
		try:
			_modernMono().setUserDictionaryEntry(word, None)
		except Exception as error:
			self._showError(error)
			return
		self.spelling.Clear()
		self.phonetics.Clear()
		self._reload()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	def __init__(self):
		super().__init__()
		preferences = gui.mainFrame.sysTrayIcon.preferencesMenu
		self._dictionaryMenu = preferences
		for item in preferences.GetMenuItems():
			label = item.GetItemLabelText().replace("&", "").casefold()
			if "speech diction" in label and item.GetSubMenu() is not None:
				self._dictionaryMenu = item.GetSubMenu()
				break
		self._menuItem = self._dictionaryMenu.Append(
			wx.ID_ANY, "Modern Mono &user dictionary..."
		)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self._open, self._menuItem)

	def _open(self, event):
		gui.mainFrame.prePopup()
		try:
			dialog = DictionaryDialog(gui.mainFrame)
			try:
				dialog.ShowModal()
			finally:
				dialog.Destroy()
		finally:
			gui.mainFrame.postPopup()

	def terminate(self):
		try:
			self._dictionaryMenu.Delete(self._menuItem.GetId())
		finally:
			super().terminate()
