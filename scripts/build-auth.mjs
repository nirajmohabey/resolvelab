import {build} from 'esbuild';
import fs from 'node:fs/promises';
await build({entryPoints:['web/auth-client-src.js'],outfile:'web/auth-client.js',bundle:true,format:'esm',platform:'browser',target:'es2022',legalComments:'linked',minify:true});
await fs.mkdir('web/licenses',{recursive:true});
await fs.copyFile('node_modules/oauth4webapi/LICENSE.md','web/licenses/oauth4webapi-LICENSE.md');
await fs.copyFile('node_modules/better-auth/LICENSE.md','web/licenses/better-auth-LICENSE.md');
console.log('Built pinned OAuth and Better Auth browser clients with retained licenses.');
