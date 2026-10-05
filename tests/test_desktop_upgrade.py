import unittest
from unittest.mock import MagicMock, patch
from local_wake import WakeDetector, ConversationGate, KeywordDetector
from actions.excel_control import validate_address, validate_formula, excel_control
from actions import browser_tabs


class WakeTests(unittest.TestCase):
    def test_keyword_waits_for_complete_word(self):
        recognizer = MagicMock()
        recognizer.AcceptWaveform.return_value = False
        recognizer.PartialResult.return_value = '{"partial":"hey jarvis"}'
        detector = KeywordDetector(recognizer)
        self.assertFalse(detector.feed(bytes(2560)))
        self.assertFalse(detector.feed(bytes(2560)))
        self.assertFalse(detector.feed(bytes(2560)))
        recognizer.AcceptWaveform.return_value = True
        recognizer.Result.return_value = '{"text":"hey jarvis"}'
        self.assertTrue(detector.feed(bytes(2560)))
        recognizer.Reset.assert_called_once()

    def test_keyword_rejects_ordinary_speech_and_hey_alone(self):
        for phrase in ("hey", "hello", "[unk]", "hey google", "service", "jar"):
            recognizer = MagicMock()
            recognizer.AcceptWaveform.return_value = True
            recognizer.Result.return_value = '{"text":"' + phrase + '"}'
            self.assertFalse(KeywordDetector(recognizer).feed(bytes(2560)))

    def test_second_engine_can_wake_when_first_misses(self):
        model = MagicMock()
        model.predict.return_value = {"hey_jarvis":0.001}
        keyword = MagicMock()
        keyword.feed.return_value = True
        detector = WakeDetector(model=model,keyword=keyword)
        self.assertTrue(detector.feed(bytes(2560)))
        self.assertEqual(detector.last_source,"keyword")
        keyword.reset.assert_called_once()

    def test_chunking_and_detection(self):
        model = MagicMock()
        model.predict.return_value = {"hey_jarvis":0.8}
        d = WakeDetector(model=model)
        self.assertFalse(d.feed(bytes(1024)))
        self.assertFalse(d.feed(bytes(1024)))
        self.assertTrue(d.feed(bytes(1024)))
        self.assertEqual(len(model.predict.call_args.args[0]),1280)
        model.reset.assert_called_once()
        self.assertEqual(len(d.pending),0)

    def test_silence_and_idle_timeout(self):
        model = MagicMock()
        model.predict.return_value = {"hey_jarvis":0.01}
        self.assertFalse(WakeDetector(model=model).feed(bytes(2560)))
        gate = ConversationGate()
        self.assertFalse(gate.awake(1))
        gate.touch(10)
        self.assertTrue(gate.awake(69))
        self.assertFalse(gate.awake(70))
        gate.touch(71)
        gate.sleep()
        self.assertFalse(gate.awake(72))


class ExcelTests(unittest.TestCase):
    def test_range_validation(self):
        self.assertEqual(validate_address("a1:d20"),"A1:D20")
        for value in ("A:A","A0","Sheet1!A1","A1,B2","=SUM(A1)"):
            with self.assertRaises(ValueError):
                validate_address(value)

    def test_formula_validation(self):
        self.assertEqual(validate_formula("=SUM(B2:B10)"),"=SUM(B2:B10)")
        for value in ('=WEBSERVICE("https://example.com")',"=cmd|' /C calc'!A0",
                      "=CALL(A1)","=[Other.xlsx]Sheet1!A1"):
            with self.assertRaises(ValueError):
                validate_formula(value)

    @patch("win32com.client.GetActiveObject")
    def test_write_shape_mismatch_does_not_write(self, connect):
        app=connect.return_value
        cell=app.Workbooks.Item.return_value.Worksheets.Item.return_value.Range.return_value
        cell.CountLarge=4
        cell.Rows.Count=2
        cell.Columns.Count=2
        cell.Value2="original"
        result=excel_control("write",workbook="test",sheet="test",address="A1:B2",data_json="[[1,2]]")
        self.assertIn("boyutu",result)
        self.assertEqual(cell.Value2,"original")

    @patch("win32com.client.GetActiveObject")
    def test_write_and_read(self, connect):
        app=connect.return_value
        book=app.Workbooks.Item.return_value
        book.Name="test"
        ws=book.Worksheets.Item.return_value
        ws.Name="sheet"
        cell=ws.Range.return_value
        cell.CountLarge=2
        cell.Rows.Count=1
        cell.Columns.Count=2
        cell.Formula=""
        self.assertIn("tamamlandı",excel_control("write",workbook="test",sheet="sheet",
                      address="A1:B1",data_json='[["Ürün",25]]'))
        self.assertEqual(cell.Value2,(("Ürün",25),))
        self.assertIn("25",excel_control("read",workbook="test",sheet="sheet",address="A1:B1"))


class TabTests(unittest.TestCase):
    def setUp(self):
        browser_tabs._known.clear()
        browser_tabs._owned.clear()

    @patch("actions.browser_tabs._snapshot",return_value=[])
    @patch("actions.browser_tabs._focus")
    def test_missing_tab_never_focuses_or_closes(self,focus,snapshot):
        self.assertIn("kapatılmadı",browser_tabs.close_tab("missing"))
        focus.assert_not_called()

    @patch("actions.browser_tabs.time.sleep")
    @patch("actions.browser_tabs._snapshot")
    def test_open_tracks_only_one_new_tab(self,snapshot,sleep):
        old=((1,(10,)),{"window_id":1},MagicMock())
        new=((1,(11,)),{"window_id":1},MagicMock())
        snapshot.side_effect=[[old],[old,new]]
        opener=MagicMock()
        key=browser_tabs.tracked_open("https://example.com",opener)
        self.assertEqual(browser_tabs._known[key],new[0])
        self.assertEqual(browser_tabs._owned,[key])
        opener.assert_called_once_with("https://example.com")

    @patch("actions.browser_tabs.time.sleep")
    @patch("actions.browser_tabs._snapshot")
    @patch("actions.browser_tabs._focus")
    @patch("pywinauto.keyboard.send_keys")
    def test_targeted_close(self,keys,focus,snapshot,sleep):
        tab=MagicMock()
        tab.is_selected.return_value=True
        row=((7,(77,)),{"window_id":7},tab)
        browser_tabs._known["abc"]=row[0]
        browser_tabs._owned.append("abc")
        snapshot.side_effect=[[row],[]]
        self.assertIn("kapandı",browser_tabs.close_tab(last_opened=True))
        tab.select.assert_called_once()
        keys.assert_called_once_with("^w")
        self.assertEqual(browser_tabs._owned,[])


if __name__=="__main__":
    unittest.main()
