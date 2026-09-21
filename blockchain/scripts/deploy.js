const hre = require('hardhat');
const fs = require('node:fs');
const path = require('node:path');

async function main() {
  const factory = await hre.ethers.getContractFactory('SupplyChain');
  const contract = await factory.deploy();
  await contract.waitForDeployment();
  const artifact = await hre.artifacts.readArtifact('SupplyChain');
  const address = await contract.getAddress();
  const network = await hre.ethers.provider.getNetwork();
  const receipt = await contract.deploymentTransaction().wait();
  const deployment = { address, chain_id: Number(network.chainId), block_number: receipt.blockNumber,
    owner: await contract.owner(), abi: artifact.abi };
  fs.writeFileSync(path.join(__dirname, '..', 'deployment.json'), JSON.stringify(deployment, null, 2) + '\n');
  console.log(`SupplyChain deployed at ${address}; chain ${network.chainId}`);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
