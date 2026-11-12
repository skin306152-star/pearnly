* sw_write —— 共享写入实验用的最小写入程序（只做实验，不进产品、不进安装包）。
* 以共享方式打开表（结构化 CDX 自动带上），按 Harbour DBFCDX 的锁方案加锁写入，索引由 DBFCDX 增量维护。
* 用法：sw_write.exe <ACTION> <DIR> <TABLE> <RECNO> <OUT> <SCHEME> [字段=值 ...]
*   CLONE   追加一行：先复制第 <RECNO> 行的全部字段，再按「字段=值」改写（APPEND 时 Harbour 自己锁表头与新行）
*   SET     改已有的第 <RECNO> 行：RLOCK（等最多 30 秒）→ 改字段 → COMMIT → 解锁
*   NEXTNO  ISRUN 取号：RLOCK 第 <RECNO> 行 → 锁内重读 → 字段 <值里给的栏> 按数字串 +1 → 写回 → 解锁；把取到的号写进 OUT
*   CALSTA  把第 <RECNO> 行 CALSTA 第 n 位置成 '0'（参数 POS=n）
* 值的写法：C 原样；N / B / I / Y 按数字；D 用 YYYYMMDD；L 用 T / F。带空格的值整体加引号。
* SCHEME：0 默认（Harbour 按表头自动选）/ 3 VFP。
#include "dbinfo.ch"
#include "fileio.ch"
REQUEST DBFCDX

PROCEDURE Main( cAction, cDir, cTable, cRec, cOut, cScheme, ... )
   LOCAL aArgs := hb_AParams(), n, i, oErr, nRec, aVals := {}, cPair, nPos, cField, cVal, nT, cNo := "", cHead0
   n := FCreate( cOut )
   IF n < 0 ; RETURN ; ENDIF
   RDDSETDEFAULT( "DBFCDX" )
   SET EXCLUSIVE OFF
   SET DELETED OFF
   IF Val( cScheme ) > 0
      Set( _SET_DBFLOCKSCHEME, Val( cScheme ) )
   ENDIF
   FOR i := 7 TO Len( aArgs )
      cPair := aArgs[ i ]
      nPos := At( "=", cPair )
      IF nPos > 1
         AAdd( aVals, { Upper( Left( cPair, nPos - 1 ) ), SubStr( cPair, nPos + 1 ) } )
      ENDIF
   NEXT
   nRec := Val( cRec )
   nT := hb_MilliSeconds()
   FWrite( n, "action=" + cAction + " table=" + cTable + " rec=" + cRec + " t0=" + Time() + hb_eol() )
   IF Upper( cAction ) == "HEADFIX"            // 恢复用：在表头锁内把年份字节、第 28 字节写成给定值（YEAR=十六进制 FLAG=十六进制）
      cHead0 := ReadHead( cDir + "\" + cTable + ".DBF" )
      FOR i := 1 TO Len( aVals )
         IF aVals[ i ][ 1 ] == "YEAR" ; cHead0 := Stuff( cHead0, 2, 1, hb_HexToStr( aVals[ i ][ 2 ] ) ) ; ENDIF
         IF aVals[ i ][ 1 ] == "FLAG" ; cHead0 := Stuff( cHead0, 29, 1, hb_HexToStr( aVals[ i ][ 2 ] ) ) ; ENDIF
      NEXT
      FWrite( n, "head_fix=" + PatchHead( cDir + "\" + cTable + ".DBF", cHead0 ) + hb_eol() )
      FClose( n )
      RETURN
   ENDIF
   IF Upper( cAction ) == "REINDEX"           // 只用于副本：独占打开整表重建（同产品现在的做法），做比对基准
      BEGIN SEQUENCE WITH {| e | Break( e ) }
         USE ( cDir + "\" + cTable ) NEW EXCLUSIVE VIA "DBFCDX"
         dbReindex()
         FWrite( n, "reindex=.T. ordcount=" + LTrim( Str( OrdCount() ) ) + " ms=" + LTrim( Str( hb_MilliSeconds() - nT ) ) + hb_eol() )
         CLOSE DATABASES
      RECOVER USING oErr
         FWrite( n, "error=" + oErr:Description + " oscode=" + LTrim( Str( oErr:OsCode ) ) + hb_eol() )
      END SEQUENCE
      FClose( n )
      RETURN
   ENDIF
   cHead0 := ReadHead( cDir + "\" + cTable + ".DBF" )   // 写前表头：之后把 Harbour 改掉的「年份字节、第 28 字节标志」写回 Express 的原值
   BEGIN SEQUENCE WITH {| e | Break( e ) }
      USE ( cDir + "\" + cTable ) NEW SHARED VIA "DBFCDX"
      FWrite( n, "used=.T. lockscheme=" + LTrim( Str( dbInfo( DBI_LOCKSCHEME ) ) ) + " records=" + LTrim( Str( LastRec() ) ) + hb_eol() )
      IF nRec == 0
         nRec := LastRec()                 // 0 = 最后一行（计时用：复制表尾那一行）
      ENDIF
      DO CASE
      CASE Upper( cAction ) == "CLONE"
         CloneAppend( n, nRec, aVals )
      CASE Upper( cAction ) == "SET"
         IF WaitLock( nRec )
            dbGoto( nRec )
            Apply( aVals )
            dbCommit()
            dbRUnlock( nRec )
            FWrite( n, "set=.T. recno=" + LTrim( Str( nRec ) ) + hb_eol() )
         ELSE
            FWrite( n, "set=.F. locked" + hb_eol() )
         ENDIF
      CASE Upper( cAction ) == "NEXTNO"
         IF WaitLock( nRec )
            dbGoto( nRec )                       // 锁内重读
            cField := aVals[ 1 ][ 1 ]
            cVal := RTrim( FieldGet( FieldPos( cField ) ) )
            cNo := Next7( cVal )
            FieldPut( FieldPos( cField ), PadR( cNo, Len( FieldGet( FieldPos( cField ) ) ) ) )
            dbCommit()
            dbRUnlock( nRec )
            FWrite( n, "nextno=" + cNo + " was=" + cVal + hb_eol() )
         ELSE
            FWrite( n, "nextno=.F. locked" + hb_eol() )
         ENDIF
      CASE Upper( cAction ) == "DELETE"
         // 给第 <RECNO> 行打删除标记（同 Express 自己撤掉分录行的做法）：锁行 → 删除 → 提交 → 解锁
         IF WaitLock( nRec )
            dbGoto( nRec )
            dbDelete()
            dbCommit()
            dbRUnlock( nRec )
            FWrite( n, "deleted=.T. recno=" + LTrim( Str( nRec ) ) + hb_eol() )
         ELSE
            FWrite( n, "deleted=.F. locked" + hb_eol() )
         ENDIF
      CASE Upper( cAction ) == "ADD"
         // 余额按增量加：锁行 → 锁内重读 → 每个「字段=增量」加上去 → 写回 → 解锁（不写写前算好的整行）
         IF WaitLock( nRec )
            dbGoto( nRec )
            PauseIf( aVals, "PAUSE1" )
            FOR i := 1 TO Len( aVals )
               nPos := FieldPos( aVals[ i ][ 1 ] )
               IF nPos > 0
                  FWrite( n, "add " + aVals[ i ][ 1 ] + " " + hb_ntos( FieldGet( nPos ) ) )
                  FieldPut( nPos, FieldGet( nPos ) + Val( aVals[ i ][ 2 ] ) )
                  FWrite( n, " -> " + hb_ntos( FieldGet( nPos ) ) + hb_eol() )
               ENDIF
            NEXT
            dbCommit()
            dbRUnlock( nRec )
         ELSE
            FWrite( n, "add=.F. locked" + hb_eol() )
         ENDIF
      CASE Upper( cAction ) == "CALSTA"
         IF WaitLock( nRec )
            dbGoto( nRec )
            nPos := Val( aVals[ 1 ][ 2 ] )
            cVal := PadR( FieldGet( FieldPos( "CALSTA" ) ), Len( FieldGet( FieldPos( "CALSTA" ) ) ) )
            cVal := Stuff( cVal, nPos, 1, "0" )
            FieldPut( FieldPos( "CALSTA" ), cVal )
            dbCommit()
            dbRUnlock( nRec )
            FWrite( n, "calsta=" + RTrim( cVal ) + hb_eol() )
         ELSE
            FWrite( n, "calsta=.F. locked" + hb_eol() )
         ENDIF
      ENDCASE
      FWrite( n, "records_after=" + LTrim( Str( LastRec() ) ) + " ms=" + LTrim( Str( hb_MilliSeconds() - nT ) ) + hb_eol() )
      CLOSE DATABASES
      IF Upper( cAction ) == "CLONE" .AND. Len( cHead0 ) == 32
         FWrite( n, "head_patch=" + PatchHead( cDir + "\" + cTable + ".DBF", cHead0 ) + hb_eol() )
      ENDIF
   RECOVER USING oErr
      FWrite( n, "error=" + oErr:Description + " op=" + oErr:Operation + " oscode=" + LTrim( Str( oErr:OsCode ) ) + " gen=" + LTrim( Str( oErr:GenCode ) ) + hb_eol() )
   END SEQUENCE
   FClose( n )
   RETURN

STATIC FUNCTION ReadHead( cFile )
   LOCAL h := FOpen( cFile, FO_READ + FO_SHARED ), c := Space( 32 )
   IF h < 0 ; RETURN "" ; ENDIF
   FRead( h, @c, 32 )
   FClose( h )
   RETURN c

STATIC FUNCTION PatchHead( cFile, cHead0 )
   // 在表头锁（VFP：0x7FFFFFFE 长 1）内把第 1 字节（年）与第 28 字节（表标志）写回写前的值；行数、月日不动
   LOCAL h := FOpen( cFile, FO_READWRITE + FO_SHARED ), nEnd := hb_MilliSeconds() + 30000, c := Space( 32 ), cOut
   IF h < 0 ; RETURN "open_failed" ; ENDIF
   DO WHILE ! hb_FLock( h, 0x7FFFFFFE, 1 )
      IF hb_MilliSeconds() > nEnd
         FClose( h )
         RETURN "lock_timeout"
      ENDIF
      hb_idleSleep( 0.1 )
   ENDDO
   FSeek( h, 0, FS_SET )
   FRead( h, @c, 32 )
   cOut := hb_StrToHex( SubStr( c, 2, 1 ) + SubStr( c, 29, 1 ) )
   IF SubStr( c, 2, 1 ) != SubStr( cHead0, 2, 1 )
      FSeek( h, 1, FS_SET ) ; FWrite( h, SubStr( cHead0, 2, 1 ), 1 )
   ENDIF
   IF SubStr( c, 29, 1 ) != SubStr( cHead0, 29, 1 )
      FSeek( h, 28, FS_SET ) ; FWrite( h, SubStr( cHead0, 29, 1 ), 1 )
   ENDIF
   hb_FUnlock( h, 0x7FFFFFFE, 1 )
   FClose( h )
   RETURN cOut + "->" + hb_StrToHex( SubStr( cHead0, 2, 1 ) + SubStr( cHead0, 29, 1 ) )

STATIC FUNCTION Next7( cVal )
   // 末尾 7 位数字 +1（ISRUN 的号是「前缀 + 7 位」，例 RR0000094 → RR0000095）
   LOCAL cHead := Left( cVal, Len( cVal ) - 7 ), nNum := Val( Right( cVal, 7 ) ) + 1
   RETURN cHead + StrZero( nNum, 7 )

STATIC FUNCTION WaitLock( nRec )
   LOCAL nEnd := hb_MilliSeconds() + 30000
   DO WHILE hb_MilliSeconds() < nEnd
      IF dbRLock( nRec )
         RETURN .T.
      ENDIF
      hb_idleSleep( 0.2 )
   ENDDO
   RETURN .F.

STATIC PROCEDURE CloneAppend( n, nRec, aVals )
   LOCAL aRow := {}, i, nNew, lDel
   dbGoto( nRec )
   lDel := Deleted()
   FOR i := 1 TO FCount()
      AAdd( aRow, FieldGet( i ) )
   NEXT
   dbAppend()                       // 共享方式：锁表头追加、新行保持锁定
   IF NetErr()
      FWrite( n, "append=.F. neterr" + hb_eol() )
      RETURN
   ENDIF
   FWrite( n, "appended_blank recno=" + LTrim( Str( RecNo() ) ) + hb_eol() )
   PauseIf( aVals, "PAUSE1" )       // 故障注入用：追加了空行、还没写内容
   FOR i := 1 TO FCount()
      FieldPut( i, aRow[ i ] )
   NEXT
   Apply( aVals )
   IF lDel
      dbDelete()                    // 原行带删除标记就照样带上（Express 写完又撤掉的分录行）
   ENDIF
   PauseIf( aVals, "PAUSE2" )       // 故障注入用：内容在缓冲里、还没提交（索引未更新）
   dbCommit()
   nNew := RecNo()
   PauseIf( aVals, "PAUSE3" )       // 故障注入用：已提交、新行还锁着
   dbRUnlock( nNew )
   FWrite( n, "append=.T. recno=" + LTrim( Str( nNew ) ) + hb_eol() )
   RETURN

STATIC PROCEDURE PauseIf( aVals, cKey )
   LOCAL i
   FOR i := 1 TO Len( aVals )
      IF aVals[ i ][ 1 ] == cKey
         hb_idleSleep( Val( aVals[ i ][ 2 ] ) )
      ENDIF
   NEXT
   RETURN

STATIC PROCEDURE Apply( aVals )
   LOCAL i, nF, cT, cV, aSt := dbStruct()
   FOR i := 1 TO Len( aVals )
      nF := FieldPos( aVals[ i ][ 1 ] )
      IF nF == 0 .OR. Left( aVals[ i ][ 1 ], 5 ) == "PAUSE" ; LOOP ; ENDIF
      cT := aSt[ nF ][ 2 ]
      cV := aVals[ i ][ 2 ]
      DO CASE
      CASE cT $ "NBIYF"
         FieldPut( nF, Val( cV ) )
      CASE cT == "D"
         FieldPut( nF, hb_SToD( cV ) )
      CASE cT == "L"
         FieldPut( nF, Upper( cV ) == "T" )
      OTHERWISE
         FieldPut( nF, cV )
      ENDCASE
   NEXT
   RETURN
