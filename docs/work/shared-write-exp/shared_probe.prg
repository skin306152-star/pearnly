* shared_probe —— 共享写入实验用的只读探针（不进产品、不进安装包）。
* 用法：shared_probe.exe <ACTION> <DIR> <TABLE> <TAG> <KEY> <OUT> <LOCKSCHEME>
*   OPEN  共享打开表（自动带结构化 CDX），报记录数、标签数、每个标签的名字 / 键 / FOR 条件
*   SEEK  共享打开，切到 <TAG>，DbSeek(<KEY>)
*   DUMP  共享打开，按 <TAG> 的索引顺序把「键值(十六进制) 行号 删除标记」逐行写到 <OUT>
* LOCKSCHEME：0 默认 / 1 Clipper / 2 Clipper2(=COMIX) / 3 VFP / 4 HB32 / 5 HB64（数字照 dbinfo.ch 的 DB_DBFLOCK_*）
#include "dbinfo.ch"
#include "fileio.ch"
REQUEST DBFCDX

PROCEDURE Main( cAction, cDir, cTable, cTag, cKey, cOut, cScheme )
   LOCAL n, i, oErr, cLine, nCount := 0
   IF cAction == NIL ; cAction := "" ; ENDIF
   IF cTag == NIL ; cTag := "x" ; ENDIF
   IF cKey == NIL ; cKey := "x" ; ENDIF
   IF Empty( cOut ) ; cOut := "shared_probe.out" ; ENDIF
   IF cScheme == NIL ; cScheme := "0" ; ENDIF
   n := FCreate( cOut )
   IF n < 0 ; RETURN ; ENDIF
   RDDSETDEFAULT( "DBFCDX" )
   SET EXCLUSIVE OFF
   SET DELETED OFF
   IF Val( cScheme ) > 0
      Set( _SET_DBFLOCKSCHEME, Val( cScheme ) )
   ENDIF
   FWrite( n, "action=" + cAction + " table=" + cTable + " scheme=" + cScheme + hb_eol() )
   BEGIN SEQUENCE WITH {| e | Break( e ) }
      USE ( cDir + "\" + cTable ) NEW SHARED READONLY VIA "DBFCDX"
      FWrite( n, "used=.T. shared=" + iif( dbInfo( DBI_SHARED ), ".T.", ".F." ) + ;
         " lockscheme=" + LTrim( Str( dbInfo( DBI_LOCKSCHEME ) ) ) + ;
         " records=" + LTrim( Str( LastRec() ) ) + " ordcount=" + LTrim( Str( OrdCount() ) ) + hb_eol() )
      IF Upper( cAction ) == "OPEN"
         FOR i := 1 TO OrdCount()
            FWrite( n, "ord " + LTrim( Str( i ) ) + " name=" + OrdName( i ) + " key=" + OrdKey( i ) + ;
               " for=" + OrdFor( i ) + " keycount=" + LTrim( Str( OrdKeyCount( i ) ) ) + hb_eol() )
         NEXT
      ELSEIF Upper( cAction ) == "SEEK"
         OrdSetFocus( cTag )
         IF DbSeek( cKey )
            FWrite( n, "seek_found=.T. recno=" + LTrim( Str( RecNo() ) ) + hb_eol() )
         ELSE
            FWrite( n, "seek_found=.F." + hb_eol() )
         ENDIF
      ELSEIF Upper( cAction ) == "DUMP"
         OrdSetFocus( cTag )
         dbGoTop()
         DO WHILE ! Eof()
            cLine := hb_StrToHex( hb_ValToStr( OrdKeyVal() ) ) + " " + LTrim( Str( RecNo() ) ) + iif( Deleted(), " D", "" )
            FWrite( n, cLine + hb_eol() )
            nCount++
            dbSkip()
         ENDDO
         FWrite( n, "dump_done=.T. rows=" + LTrim( Str( nCount ) ) + hb_eol() )
      ENDIF
      CLOSE DATABASES
   RECOVER USING oErr
      FWrite( n, "error=" + oErr:Description + " op=" + oErr:Operation + " oscode=" + LTrim( Str( oErr:OsCode ) ) + hb_eol() )
   END SEQUENCE
   FClose( n )
   RETURN
