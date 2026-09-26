* Pearnly cdx_reindex_runner —— Express FoxPro/DBF 账套的后台 CDX 重建 + 索引回查。
*
* 为什么存在:companion 用 python dbf 库写 DBF 记录后,Express 仍看不到该单,因为它靠
* 结构化复合索引 CDX 导航,而 python 侧 reindex 对 CDX 无效。本工具用 Harbour DBFCDX
* 打开 Express 现存 CDX(tag 名/key 全保留)→ REINDEX 重建同名标签 → DbSeek 回查命中,
* 等价于 Express 自带 PACK/Reindex 的索引效果,但无需打开 Express、无需用户值守。
*
* ★ 必须 GTNUL 编译(hbmk2 ... -gtnul -static -comp=mingw):默认 GTWIN console 驱动在
*   companion 后台无窗口子进程里启动即 hang 死(抓不到控制台)。
*
* 调用:cdx_reindex_runner.exe <ACTION> <DIR> <TABLE> [<TAG> <KEY> <LOG>]
*   REINDEX —— 重建该表全部 CDX 标签;输出 reindex_done=.T. 或 error=...
*   SEEK    —— 切到 <TAG> 索引,DbSeek(<KEY>);输出 seek_found=.T./.F.(+命中字段)
*   TAGS    —— 枚举标签(诊断用)
* 传 <LOG> 时把结果写到指定文件(companion 走本地临时文件,不在共享账套留垃圾);
* 缺省写 <DIR>\cdx_reindex_runner.log。可选参数可省略(NIL 防御兜底)。

REQUEST DBFCDX

PROCEDURE Main(cAction, cDir, cTable, cTag, cKey, cLog)
   LOCAL n, lFound, i, cPath, cStkCod, cDocNum, cSeqNum, cStkDes, oErr
   IF cAction == NIL ; cAction := "" ; ENDIF
   IF cDir == NIL ; cDir := "" ; ENDIF
   IF cTable == NIL ; cTable := "" ; ENDIF
   IF cTag == NIL ; cTag := "" ; ENDIF
   IF cKey == NIL ; cKey := "" ; ENDIF
   IF Empty(cLog)
      cLog := cDir + "\\cdx_reindex_runner.log"
   ENDIF
   n := FOpen(cLog, 1)
   IF n < 0
      n := FCreate(cLog)
   ELSE
      FSeek(n, 0, 2)
   ENDIF
   IF n < 0
      RETURN
   ENDIF
   RDDSETDEFAULT("DBFCDX")
   SET EXCLUSIVE ON
   FWrite(n, "action=" + cAction + " table=" + cTable + " tag=" + cTag + hb_eol())
   BEGIN SEQUENCE
      cPath := cDir + "\\" + cTable
      USE (cPath) NEW EXCLUSIVE VIA "DBFCDX"
      FWrite(n, "used=.T. ordcount=" + LTrim(Str(OrdCount())) + " records=" + LTrim(Str(LastRec())) + hb_eol())
      IF Upper(cAction) == "TAGS"
         FOR i := 1 TO OrdCount()
            FWrite(n, "ord " + LTrim(Str(i)) + " name=" + OrdName(i) + " key=" + OrdKey(i) + hb_eol())
         NEXT
      ELSEIF Upper(cAction) == "SEEK"
         OrdSetFocus(cTag)
         lFound := DbSeek(cKey)
         IF lFound
            cStkCod := ""
            cDocNum := ""
            cSeqNum := ""
            cStkDes := ""
            IF FieldPos("STKCOD") > 0
               cStkCod := FieldGet(FieldPos("STKCOD"))
            ENDIF
            IF FieldPos("DOCNUM") > 0
               cDocNum := FieldGet(FieldPos("DOCNUM"))
            ENDIF
            IF FieldPos("SEQNUM") > 0
               cSeqNum := FieldGet(FieldPos("SEQNUM"))
            ENDIF
            IF FieldPos("STKDES") > 0
               cStkDes := FieldGet(FieldPos("STKDES"))
            ENDIF
            FWrite(n, "seek_found=.T. recno=" + LTrim(Str(RecNo())) + " stkcod=" + cStkCod + " doc=" + cDocNum + " seq=" + cSeqNum + " stkdes=" + cStkDes + hb_eol())
         ELSE
            FWrite(n, "seek_found=.F." + hb_eol())
         ENDIF
      ELSEIF Upper(cAction) == "REINDEX"
         REINDEX
         FWrite(n, "reindex_done=.T. ordcount=" + LTrim(Str(OrdCount())) + " records=" + LTrim(Str(LastRec())) + hb_eol())
      ELSE
         FWrite(n, "unknown_action" + hb_eol())
      ENDIF
      CLOSE DATABASES
   RECOVER USING oErr
      FWrite(n, "error=" + oErr:Description + hb_eol())
   END SEQUENCE
   FClose(n)
   RETURN
