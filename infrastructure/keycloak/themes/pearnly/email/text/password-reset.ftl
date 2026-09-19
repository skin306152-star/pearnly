<#ftl output_format="plainText">
${msg("mailResetTitle")}

${msg("mailGreeting")}
${msg("mailResetIntro")}
${msg("mailAccount")}: ${(user.email)!user.username}
${msg("mailExpiry", linkExpiration?c)}
${link}

${msg("mailSafetyBody")}
${msg("mailContact")}
${msg("mailEmail")}: hello@pearnly.com
${msg("mailPhone")}: +66 86-889-2228
${msg("mailTeam")}
