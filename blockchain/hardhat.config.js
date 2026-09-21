require('@nomicfoundation/hardhat-ethers');
const { subtask } = require('hardhat/config');
const { TASK_COMPILE_SOLIDITY_GET_SOLC_BUILD } = require('hardhat/builtin-tasks/task-names');

// Use the pinned npm compiler, avoiding a second remote compiler download.
subtask(TASK_COMPILE_SOLIDITY_GET_SOLC_BUILD).setAction(async ({ solcVersion }, hre, runSuper) => {
  if (solcVersion === '0.8.28') return {
    compilerPath: require.resolve('solc/soljson.js'), isSolcJs: true,
    version: '0.8.28', longVersion: require('solc').version()
  };
  return runSuper();
});

module.exports = {
  solidity: { version: '0.8.28', settings: { optimizer: { enabled: true, runs: 200 } } },
  networks: { localhost: { url: 'http://127.0.0.1:8545', chainId: 31337 } }
};
