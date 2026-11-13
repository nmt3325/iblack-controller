import unittest
from index_firmware import Image,literal_address,branch_target,safe
class ArmIndexTests(unittest.TestCase):
    def test_positive_literal(self):self.assertEqual(literal_address(0xe59f0020,0x1000),0x1028)
    def test_negative_literal(self):self.assertEqual(literal_address(0xe51f0020,0x1000),0xfe8)
    def test_reject_non_literal(self):self.assertIsNone(literal_address(0xe5900020,0x1000))
    def test_forward_call(self):self.assertEqual(branch_target(0xeb000004,0x1000),0x1018)
    def test_backward_call(self):self.assertEqual(branch_target(0xebfffffc,0x1000),0xff8)
    def test_not_call(self):self.assertIsNone(branch_target(0xea000004,0x1000))
    def test_mac_redaction(self):self.assertEqual(safe('aa:bb:cc:dd:ee:ff'),'<MAC>')
    def test_secret_redaction(self):self.assertIn('omitted',safe('password=example'))
    def test_suffix_reference_excludes_unreferenced_prefix(self):
        im=Image.__new__(Image);im.strings=[{'address':0x2000,'text':'prefix tail','section':'.rodata'}];im.starts=[0x2000];im.symbols=[];im.prologues=[]
        im.references={0x2000:[{'instruction':0x1000,'literal_pool':0x1020,'pointer':0x2007,'register':0}]}
        self.assertEqual(im.find('prefix')['matches'][0]['reference_count'],0)
        self.assertEqual(im.find('tail')['matches'][0]['references'][0]['exact_text'],'tail')
    def test_string_boundary(self):
        im=Image.__new__(Image);im.strings=[{'address':0x2000,'text':'abcd','section':'.rodata'}];im.starts=[0x2000]
        self.assertIsNotNone(im.string_at(0x2003));self.assertIsNone(im.string_at(0x2004))
if __name__=='__main__':unittest.main()
