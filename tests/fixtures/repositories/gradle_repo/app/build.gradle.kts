plugins {
    alias(libs.plugins.kotlin.jvm)
}

dependencies {
    implementation(project(":lib"))
    implementation(libs.kafka.streams)
}
