# book-snapshot.ps1 -Src <账套目录> -Tag <名>：只读把整本账套拷到 C:\PearnlyStress\_book_record\<Tag>（robocopy 共享读，不加锁、不写源）。以会话用户身份跑（网络盘权限在它身上）。
param([string]$Src, [string]$Tag)
$dst = "C:\PearnlyStress\_book_record\$Tag"
robocopy $Src $dst /E /R:2 /W:5 /NP /NDL /NFL /COPY:DT /DCOPY:T /XJ /LOG:"C:\PearnlyStress\_acc_cc\snap-$Tag.log" | Out-Null
"rc=$LASTEXITCODE" | Out-File -Encoding utf8 "C:\PearnlyStress\_acc_cc\snap-$Tag.rc"
