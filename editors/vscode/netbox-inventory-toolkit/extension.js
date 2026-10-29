'use strict';

const vscode = require('vscode');
const {
  LanguageClient,
  RevealOutputChannelOn,
  TransportKind,
} = require('vscode-languageclient/node');

let client;
let outputChannel;

function configuration() {
  return vscode.workspace.getConfiguration('netboxInventory');
}

function workspaceFolder() {
  const folders = vscode.workspace.workspaceFolders || [];
  return folders.find((folder) => folder.uri.scheme === 'file') || folders[0];
}

function documentSelector() {
  return [
    '**/{manual,generated}/**/*.yaml',
    '**/{manual,generated}/**/*.yml',
  ].map((pattern) => ({ scheme: 'file', language: 'yaml', pattern }));
}

function serverOptions(folder) {
  const config = configuration();
  const command = config.get('server.command', 'uvx');
  const args = [...config.get('server.args', [])];
  const inventory = config.get('inventory', '');
  if (inventory) {
    args.push('--inventory', inventory);
  }
  const cwd = config.get('server.cwd', '') || folder.uri.fsPath;
  return {
    command,
    args,
    transport: TransportKind.stdio,
    options: {
      cwd,
      env: process.env,
    },
  };
}

function clientOptions(folder) {
  return {
    documentSelector: documentSelector(),
    workspaceFolder: folder,
    outputChannel,
    revealOutputChannelOn: RevealOutputChannelOn.Never,
    synchronize: {
      fileEvents: vscode.workspace.createFileSystemWatcher(
        '**/{manual,generated}/**/*.{yaml,yml}'
      ),
    },
  };
}

async function stopClient() {
  if (client) {
    const current = client;
    client = undefined;
    await current.stop();
  }
}

async function startClient(context) {
  const folder = workspaceFolder();
  if (!folder || folder.uri.scheme !== 'file') {
    return;
  }

  await stopClient();
  client = new LanguageClient(
    'netboxInventory',
    'NetBox inventory toolkit',
    serverOptions(folder),
    clientOptions(folder)
  );
  context.subscriptions.push(client);
  await client.start();
}

async function restartClient(context) {
  try {
    await startClient(context);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    vscode.window.showErrorMessage(`NetBox inventory toolkit: ${message}`);
  }
}

function activate(context) {
  outputChannel = vscode.window.createOutputChannel('NetBox inventory toolkit');
  context.subscriptions.push(outputChannel);
  context.subscriptions.push(
    vscode.commands.registerCommand('netboxInventory.restart', () =>
      restartClient(context)
    )
  );
  restartClient(context);
}

function deactivate() {
  return stopClient();
}

module.exports = {
  activate,
  deactivate,
};
